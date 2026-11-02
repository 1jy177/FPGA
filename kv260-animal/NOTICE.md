# 许可与第三方声明

## 示例数据及派生结果

B. Dogdas, D. Stout, A. F. Chatziioannou and R. M. Leahy, **Digimouse: a 3D whole body mouse atlas from CT and cryosection data**, Phys. Med. Biol. 52, 577–587 (2007). DOI: [10.1088/0031-9155/52/3/003](https://doi.org/10.1088/0031-9155/52/3/003)。来源为 [USC Biomedical Imaging Group](https://neuroimage.usc.edu/neuro/Digimouse)。

原始 ZIP 内的许可和官网声明均为 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。`data/original/digimouse/` 保留原始 ZIP 和许可全文。`data/digimouse/` 保留署名、标注映射和修改说明。

派生处理包括逐标注裁剪、带物理原点的连续场滤波、uint16 编码、MC 重建、独立显示 LOD 和器官配色。数据和派生模型按 CC BY-SA 4.0 分发。允许商业使用，但必须保留署名、来源、许可链接和修改说明，并遵守相同方式共享要求。不得将第三方图谱或其派生模型作为排他所有的数据出售。作者和大学名称不代表对本工程的背书。

自研代码和第三方数据应分别说明授权，不把数据许可写成自研算法的归属声明。

## 保留的历史胚胎数据

开发机上的旧数据来自 Brian Metscher（University of Vienna），《Whole mouse embryo microCT image with nuclear staining (Bleikern)》，DOI 为 [10.5281/zenodo.4034294](https://doi.org/10.5281/zenodo.4034294)，许可为 CC BY 4.0。旧版交付包和历史报告继续保留此声明。该数据不再作为默认展示。

## Marching Cubes 表

表来自 PCL 1.12 的 Marching Cubes 实现，采用 BSD 3-Clause 许可。完整声明见 `shared/LICENSE.tables`。随硬件二进制交付时也要保留该声明。

## 开发工具及运行库

AMD/Xilinx 生成的 IP、驱动和 bitstream 受相应工具/IP 许可约束。工程包不附带 Vivado 安装器、商业工具许可证、操作系统镜像或 PYNQ 环境。NumPy、SciPy、VTK 等通过各自上游渠道安装，并遵循各自许可。

本文件不替项目所有者决定自有源码的授权范围；自有源码和商业交付条款由项目所有者与接收方约定。本说明不替代法律审查。
