#!/usr/bin/env python3
"""静态查看器 + 等值面阈值「实时重建」服务。

为什么需要这个文件
------------------
浏览器要能改等值面阈值（iso）并立刻看到新形状，就必须有人在板子上接收请求、
驱动 FPGA 重算、再把新网格交给浏览器。`python -m http.server` 只能发静态文件，
处理不了这种请求，所以把两件事合成一个进程。

为什么阈值可以「实时」而不用重新编译电路
--------------------------------------
`iso` 是 kernel 的 **s_axilite 运行时参数**（寄存器偏移 0x50），不是编译期常量。
改它只需要再启动一次 kernel，不需要 HLS 综合或 Vivado 重新出 bit。

为什么只重算「一个器官」
----------------------
16 个器官全部重算是 15 秒级；单个器官是 0.02~5.3 秒级（心脏 0.14 s、眼睛 0.02 s），
这样才能做到拖动即变。

为什么大器官要降采样
------------------
body 完整网格 28.6 MB / 167 万面，skeleton 24.3 MB / 142 万面。整块传给浏览器会让
拖动响应变慢，所以对超过 `--preserve-small` 面的器官做一次网格聚类降采样。
板子上没有 VTK（VTK 是工作站的依赖），因此这里用一个等价的 numpy 网格聚类：
按体素格把顶点合并成簇，取簇内均值作为代表点，再丢掉退化三角形。
**这是显示用的近似，不影响完整网格文件；预先算好的 `.display.mesh` 仍由工作站上的
VTK 二次误差聚类生成，两者在细节上会有差异，报告中已注明。**

用法
----
  sudo -n /usr/local/share/pynq-venv/bin/python3 scripts/scene_server.py --port 8870
  curl -s -X POST localhost:8870/api/rebuild -d '{"layer":"heart","iso":42000}'
"""

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "software"))

# 没有预计算 divisions 时的兜底密度（沿最长轴切几格），取自 make_display.py 的默认值
FALLBACK_LONGEST = {"body": 700, "skeleton": 1000}
DEFAULT_LONGEST = 240
MAGIC = 0x344C5856          # VXL4 头魔数
ISO_OFFSET = 84             # VXL4 头里 iso(uint16 LE) 的偏移


def read_header(path: Path, n: int = 128) -> bytes:
    with path.open("rb") as stream:
        return stream.read(n)


def volume_iso(path: Path) -> int:
    """读体数据自带的等值面阈值，作为滑块初值。"""
    try:
        header = read_header(path)
        if len(header) >= ISO_OFFSET + 2:
            magic = int.from_bytes(header[0:4], "little")
            if magic == MAGIC:
                return int.from_bytes(header[ISO_OFFSET:ISO_OFFSET + 2], "little")
    except OSError:
        pass
    return 32768


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cluster(points: np.ndarray, faces: np.ndarray, grid):
    """网格聚类降采样（显示用近似）。

    把包围盒切成 grid=(nx,ny,nz) 的三维格，落在同一格的顶点合并为一个，
    代表点取簇内均值；随后丢弃三点中任意两点相同的退化三角形。
    grid 直接沿用 scene.json 里预计算 LOD 的 divisions，使拖动阈值后的网格
    密度与静态显示网格一致。
    """
    lo = points.min(axis=0)
    hi = points.max(axis=0)
    extent = np.maximum(hi - lo, 1e-9)
    grid = np.maximum(2, np.asarray(grid, dtype=np.int64))

    cell = extent / grid
    idx = np.floor((points - lo) / cell).astype(np.int64)
    np.clip(idx, 0, grid - 1, out=idx)
    key = (idx[:, 0] * grid[1] + idx[:, 1]) * grid[2] + idx[:, 2]

    uniq, inverse = np.unique(key, return_inverse=True)
    count = len(uniq)
    counts = np.bincount(inverse, minlength=count).astype(np.float64)
    sums = np.empty((count, 3))
    for axis in range(3):
        sums[:, axis] = np.bincount(inverse, weights=points[:, axis], minlength=count)
    rep = sums / counts[:, None]

    mapped = inverse[faces]
    keep = ((mapped[:, 0] != mapped[:, 1])
            & (mapped[:, 0] != mapped[:, 2])
            & (mapped[:, 1] != mapped[:, 2]))
    return rep.astype("<f4"), mapped[keep].astype("<u4"), grid.tolist()


def cluster_to_target(points: np.ndarray, faces: np.ndarray, base_grid, target: int,
                      max_iter: int = 3):
    """按目标面数自校准的聚类降采样。

    单次格子聚类消减得没有 VTK 二次误差聚类那么狠，直接沿用 divisions 会得到
    比静态显示网格大好几倍的网格。这里以静态 LOD 的面数为目标迭代收紧格子：
    面数与格数近似成平方关系，所以每轮把每轴格数除以 sqrt(实际/目标)。
    """
    grid = np.maximum(2, np.asarray(base_grid, dtype=np.int64))
    rep, mapped, used = cluster(points, faces, grid)
    for _ in range(max_iter - 1):
        if len(mapped) <= target:
            break
        scale = max(1.15, float(np.sqrt(len(mapped) / max(target, 1))) * 1.02)
        grid = np.maximum(2, np.round(grid / scale).astype(np.int64))
        rep, mapped, used = cluster(points, faces, grid)
    return rep, mapped, used


class Service:
    """持有 FPGA overlay，串行处理重建请求。"""

    def __init__(self, scene_dir: Path, data: Path, bit: Path, buffer_mb: int,
                 engines: int, preserve_small: int, state_path: Path,
                 url_prefix: str):
        self.scene_dir = scene_dir
        self.url_prefix = url_prefix.rstrip("/")
        self.data = data
        self.bit = bit
        self.buffer_mb = buffer_mb
        self.engines = engines
        self.preserve_small = preserve_small
        self.state_path = state_path
        self.lock = threading.Lock()
        self.engine = None
        self.dataset = json.loads((data / "dataset.json").read_text(encoding="utf-8"))
        self.layers = {x["id"]: x for x in self.dataset["layers"]}
        self.scene_path = scene_dir / "scene.json"
        self.scene = json.loads(self.scene_path.read_text(encoding="utf-8"))
        self.live_dir = scene_dir / "live"
        self.live_dir.mkdir(exist_ok=True)
        os.chmod(self.live_dir, 0o777)
        self.state = {}
        if state_path.is_file():
            try:
                self.state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.state = {}
        self.iso_default = {}
        for lid, meta in self.layers.items():
            self.iso_default[lid] = volume_iso(data / meta["input"])
        # 预计算 LOD 用的 divisions 与面数：用于让拖动阈值后的网格密度与静态 LOD 对齐
        self.divisions = {}
        self.display_triangles = {}
        for layer in self.scene["layers"]:
            display = layer.get("display") or {}
            if display.get("divisions"):
                self.divisions[layer["id"]] = [int(x) for x in display["divisions"]]
            self.display_triangles[layer["id"]] = int(
                (display.get("triangles") or layer["triangles"]))

    def grid_for(self, layer_id: str, points: np.ndarray):
        """返回降采样格数：优先复用预计算 LOD 的 divisions。"""
        if layer_id in self.divisions:
            return self.divisions[layer_id]
        lo, hi = points.min(axis=0), points.max(axis=0)
        extent = np.maximum(hi - lo, 1e-9)
        longest = FALLBACK_LONGEST.get(layer_id, DEFAULT_LONGEST)
        return np.maximum(2, np.ceil(extent / extent.max() * longest)).astype(np.int64).tolist()

    def get_engine(self):
        if self.engine is None:
            from pynq_mc import Reconstructor  # 只有板子上才有 pynq
            self.engine = Reconstructor(self.bit, backend="pynq",
                                        engines=self.engines,
                                        buffer_mb=self.buffer_mb)
        return self.engine

    def rebuild(self, layer_id: str, iso: int):
        meta = self.layers.get(layer_id)
        if meta is None:
            raise KeyError(f"未知图层 {layer_id}")
        if not 0 <= iso <= 65535:
            raise ValueError("iso 必须在 0..65535")

        started = time.perf_counter()
        source = self.data / meta["input"]
        raw = self.live_dir / f"{layer_id}.full.tmp"
        target = self.live_dir / f"{layer_id}.mesh"

        engine = self.get_engine()
        mesh = engine.reconstruct(source, iso=iso,
                                  expected_sha256=meta.get("sha256"),
                                  mesh_path=raw)
        vertices, triangles = len(mesh.points), len(mesh.faces)
        if not vertices or not triangles:
            raise ValueError(f"{layer_id} 在 iso={iso} 处没有生成面片")

        bounds = [mesh.points.min(axis=0).tolist(), mesh.points.max(axis=0).tolist()]
        simplified = False
        if triangles > self.preserve_small:
            pts = np.asarray(mesh.points)
            grid = self.grid_for(layer_id, pts)
            goal = self.display_triangles.get(layer_id) or self.preserve_small
            points, faces, grid = cluster_to_target(pts, np.asarray(mesh.faces), grid, goal)
            # 先写临时文件再原子替换：否则并发的静态 GET 可能读到写了一半的网格
            staged = self.live_dir / f"{layer_id}.cluster.tmp"
            with staged.open("wb") as stream:
                points.tofile(stream)
                faces.tofile(stream)
            os.replace(staged, target)
            vertices, triangles = len(points), len(faces)
            bounds = [points.min(axis=0).tolist(), points.max(axis=0).tolist()]
            simplified = True
        else:
            os.replace(raw, target)
            grid = None
        if raw.exists():
            raw.unlink()

        os.chmod(target, 0o666)
        entry = {
            "layer": layer_id,
            "iso": iso,
            "file": f"{self.url_prefix}/live/{target.name}",
            "vertices": vertices,
            "triangles": triangles,
            "sha256": digest(target),
            "bounds": bounds,
            "simplified": simplified,
            "divisions": grid,
            "rebuild_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "engine_s": round(float(mesh.timing.get("engine_observed_s_sum", 0.0)), 3),
            "input_s": round(float(mesh.timing.get("input_s", 0.0)), 3),
            "slabs": mesh.timing.get("slabs"),
        }
        self.state[layer_id] = entry
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        os.chmod(self.state_path, 0o666)
        return entry

    def reset(self, layer_id: str):
        """撤销单图层的阈值改动，回到 scene.json 里的原始网格。"""
        self.state.pop(layer_id, None)
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        os.chmod(self.state_path, 0o666)
        layer = next(x for x in self.scene["layers"] if x["id"] == layer_id)
        return {k: layer[k] for k in ("file", "vertices", "triangles", "sha256", "bounds")}


class Handler(SimpleHTTPRequestHandler):
    service: Service = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Handler.service.scene_dir.parent), **kwargs)

    def end_headers(self):
        # 实时网格的文件名固定（live/<器官>.mesh），不禁止缓存的话浏览器会拿旧文件，
        # 导致前端校验到"网格长度异常"。
        if "/live/" in self.path or self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            return self._json({"iso_default": self.service.iso_default,
                               "live": self.service.state})
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/"):
            return self._json({"error": "未知接口"}, HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return self._json({"error": "请求体不是合法 JSON"}, HTTPStatus.BAD_REQUEST)

        if self.path.startswith("/api/reset"):
            try:
                return self._json(self.service.reset(body.get("layer", "")))
            except (KeyError, StopIteration):
                return self._json({"error": "未知图层"}, HTTPStatus.BAD_REQUEST)

        if self.path.startswith("/api/rebuild"):
            layer = body.get("layer")
            iso = body.get("iso")
            if not isinstance(iso, (int, float)):
                return self._json({"error": "iso 必须是数字"}, HTTPStatus.BAD_REQUEST)
            # 一次只跑一个重建：FPGA 是独占资源，排队反而让响应时间不可预测
            if not self.service.lock.acquire(blocking=False):
                return self._json({"error": "上一个重建还没结束"}, HTTPStatus.TOO_MANY_REQUESTS)
            try:
                return self._json(self.service.rebuild(layer, int(iso)))
            except (KeyError, ValueError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:                      # noqa: BLE001
                return self._json({"error": f"{type(exc).__name__}: {exc}"},
                                  HTTPStatus.INTERNAL_SERVER_ERROR)
            finally:
                self.service.lock.release()

        return self._json({"error": "未知接口"}, HTTPStatus.NOT_FOUND)


def main():
    parser = argparse.ArgumentParser(description="静态查看器 + 阈值实时重建服务")
    parser.add_argument("--port", type=int, default=8870)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--scene", type=Path, default=ROOT / "output/digimouse",
                        help="含 scene.json 与网格的场景目录（其父目录作为静态根）")
    parser.add_argument("--url-prefix", default="/digimouse",
                        help="场景在 HTTP 上的挂载路径，用于拼重建后的网格 URL")
    parser.add_argument("--data", type=Path, default=ROOT / "data/digimouse")
    parser.add_argument("--bit", type=Path, default=ROOT / "overlays/mc_kv260.bit")
    parser.add_argument("--buffer-mb", type=int, default=32)
    parser.add_argument("--engines", type=int, default=2)
    parser.add_argument("--preserve-small", type=int, default=200000,
                        help="面数不超过此值的器官直接送完整网格，不做降采样")
    args = parser.parse_args()

    Handler.service = Service(args.scene, args.data, args.bit, args.buffer_mb,
                              args.engines, args.preserve_small,
                              args.scene / "live/state.json", args.url_prefix)
    # PYNQ 的 Overlay() 会在构造时取 asyncio 事件循环，只能放在主线程做一次；
    # 放进请求线程会报 "There is no current event loop in thread ..."。
    try:
        started = time.perf_counter()
        Handler.service.get_engine()
        print(f"FPGA overlay 已就绪（{time.perf_counter() - started:.2f} s）", flush=True)
    except Exception as exc:                          # noqa: BLE001
        print(f"警告：overlay 初始化失败，阈值重建将不可用：{exc}", flush=True)

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"static root : {args.scene.parent}", flush=True)
    print(f"scene       : {args.scene}  (URL {args.url_prefix}/)", flush=True)
    print(f"listening   : http://{args.bind}:{args.port}{args.url_prefix}/", flush=True)
    print(f"阈值重建接口: POST /api/rebuild  {{\"layer\":\"heart\",\"iso\":42000}}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
