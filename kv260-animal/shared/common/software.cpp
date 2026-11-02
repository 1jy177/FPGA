#include "software.hpp"

#include <cmath>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <unordered_map>

#include "mc_tri_table.hpp"

std::uint32_t crc32(const std::uint8_t* data, std::size_t size) {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ (0xEDB88320U & (0U - (crc & 1U)));
        }
    }
    return crc ^ 0xFFFFFFFFU;
}

VolumeData load_volume_bin(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("cannot open volume: " + path);
    VolumeData volume;
    input.read(reinterpret_cast<char*>(&volume.header), sizeof(volume.header));
    if (!input || volume.header.magic != kVolumeMagic ||
        volume.header.version != kVolumeVersion ||
        volume.header.header_bytes != kVolumeHeaderBytes ||
        volume.header.data_type != 2) {
        throw std::runtime_error("invalid VXL4 uint16 header: " + path);
    }
    const std::uint64_t expected = static_cast<std::uint64_t>(volume.header.width) *
                                   volume.header.height * volume.header.depth;
    if (expected * 2 != volume.header.payload_bytes) {
        throw std::runtime_error("invalid VXL4 payload size");
    }
    volume.voxels.resize(static_cast<std::size_t>(expected));
    input.read(reinterpret_cast<char*>(volume.voxels.data()), volume.voxels.size()*2);
    if (!input) throw std::runtime_error("truncated VXL4 payload");
    if (crc32(reinterpret_cast<const std::uint8_t*>(volume.voxels.data()), volume.voxels.size()*2) != volume.header.payload_crc32) {
        throw std::runtime_error("VXL4 CRC32 mismatch");
    }
    return volume;
}

void save_volume_bin(const std::string& path, VolumeData volume) {
    volume.header.magic = kVolumeMagic;
    volume.header.version = kVolumeVersion;
    volume.header.header_bytes = kVolumeHeaderBytes;
    volume.header.data_type = 2;
    volume.header.payload_bytes = volume.voxels.size()*2;
    volume.header.payload_crc32 = crc32(reinterpret_cast<const std::uint8_t*>(volume.voxels.data()), volume.voxels.size()*2);
    std::ofstream output(path, std::ios::binary);
    if (!output) throw std::runtime_error("cannot create volume: " + path);
    output.write(reinterpret_cast<const char*>(&volume.header), sizeof(volume.header));
    output.write(reinterpret_cast<const char*>(volume.voxels.data()), volume.voxels.size()*2);
    if (!output) throw std::runtime_error("cannot write volume: " + path);
}

namespace {

constexpr std::uint8_t kEdgeLowOffset[12][3] = {
    {0, 0, 0}, {1, 0, 0}, {0, 1, 0}, {0, 0, 0},
    {0, 0, 1}, {1, 0, 1}, {0, 1, 1}, {0, 0, 1},
    {0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0},
};

constexpr std::uint8_t kEdgeAxis[12] = {0, 1, 0, 1, 0, 1, 0, 1, 2, 2, 2, 2};
constexpr std::uint8_t kEdgeCornersV2[12][2] = {
    {0, 1}, {1, 2}, {3, 2}, {0, 3},
    {4, 5}, {5, 6}, {7, 6}, {4, 7},
    {0, 4}, {1, 5}, {2, 6}, {3, 7},
};

std::uint16_t interpolate_q16(std::uint16_t a, std::uint16_t b, std::uint16_t iso) {
    if ((a >= iso) == (b >= iso)) return 0;
    const std::uint32_t d = a > b ? a-b : b-a;
    const std::uint32_t distance = iso > a ? iso-a : a-iso;
    // Independent ordinary integer division reference for the hardware's
    // fully unrolled restoring divider. Endpoint 65535 decodes to exactly 1.
    return static_cast<std::uint16_t>((distance*65535U + d/2U)/d);
}

void packet_set_bits(CellPacketV3& packet, unsigned low, unsigned width, std::uint64_t value) {
    const unsigned word = low / 64;
    const unsigned shift = low % 64;
    const std::uint64_t mask = width == 64 ? ~0ULL : ((1ULL << width) - 1ULL);
    packet.word[word] |= (value & mask) << shift;
    if (shift + width > 64) {
        packet.word[word + 1] |= (value & mask) >> (64 - shift);
    }
}

std::uint64_t packet_get_bits(const CellPacketV3& packet, unsigned low, unsigned width) {
    const unsigned word = low / 64;
    const unsigned shift = low % 64;
    const std::uint64_t mask = width == 64 ? ~0ULL : ((1ULL << width) - 1ULL);
    std::uint64_t value = packet.word[word] >> shift;
    if (shift + width > 64) value |= packet.word[word + 1] << (64 - shift);
    return value & mask;
}

std::uint64_t make_v3_vertex_token(
    std::uint32_t cell_x,
    std::uint32_t cell_y,
    std::uint32_t cell_z,
    std::uint8_t edge,
    std::uint16_t t_q16) {
    std::uint64_t xyz[3] = {cell_x + kEdgeLowOffset[edge][0],
                          cell_y + kEdgeLowOffset[edge][1],
                          cell_z + kEdgeLowOffset[edge][2]};
    unsigned axis = kEdgeAxis[edge];
    // All incident edges meeting at a grid node must share one token.
    // This prevents duplicated endpoint vertices and removes degenerate
    // triangles, without moving the reconstructed surface.
    if (t_q16 == 65535) { ++xyz[axis]; axis = 3; t_q16 = 0; }
    else if (t_q16 == 0) axis = 3;
    const auto x = xyz[0], y = xyz[1], z = xyz[2];
    return (x & 0xFFFULL) |
           ((y & 0xFFFULL) << 12) |
           ((z & 0xFFFULL) << 24) |
           ((static_cast<std::uint64_t>(axis) & 0x3ULL) << 36) |
           ((static_cast<std::uint64_t>(t_q16) & 0xFFFFULL) << 38);
}

std::array<float, 3> decode_point(const VolumeBinHeader& header, std::uint64_t token) {
    const float xyz[3] = {
        static_cast<float>(token & 0xFFFULL),
        static_cast<float>((token >> 12) & 0xFFFULL),
        static_cast<float>((token >> 24) & 0xFFFULL),
    };
    const std::uint32_t axis = static_cast<std::uint32_t>((token >> 36) & 0x3ULL);
    const float t = static_cast<float>((token >> 38) & 0xFFFFULL) / 65535.0F;
    float local[3] = {xyz[0], xyz[1], xyz[2]};
    if (axis < 3) local[axis] += t;
    for (int i = 0; i < 3; ++i) local[i] *= header.spacing[i];

    std::array<float, 3> world{};
    for (int row = 0; row < 3; ++row) {
        world[row] = header.origin[row];
        for (int col = 0; col < 3; ++col) {
            world[row] += header.direction[row * 3 + col] * local[col];
        }
    }
    return world;
}

} // namespace

std::vector<CellPacketV3> build_cell_packets_reference(
    const VolumeData& volume,
    std::uint32_t z_begin,
    std::uint32_t z_end) {
    const auto& h = volume.header;
    if (h.width < 2 || h.height < 2 || h.depth < 2 || z_begin >= z_end || z_end >= h.depth) {
        throw std::runtime_error("invalid V3 reference bounds");
    }
    std::vector<CellPacketV3> packets;
    const std::uint64_t slice = static_cast<std::uint64_t>(h.width) * h.height;
    for (std::uint32_t z = z_begin; z < z_end; ++z) {
        for (std::uint32_t y = 0; y + 1 < h.height; ++y) {
            for (std::uint32_t x = 0; x + 1 < h.width; ++x) {
                const std::uint64_t base = x + static_cast<std::uint64_t>(y) * h.width +
                                           static_cast<std::uint64_t>(z) * slice;
                const std::uint16_t c[8] = {
                    volume.voxels[base], volume.voxels[base + 1],
                    volume.voxels[base + h.width + 1], volume.voxels[base + h.width],
                    volume.voxels[base + slice], volume.voxels[base + slice + 1],
                    volume.voxels[base + slice + h.width + 1], volume.voxels[base + slice + h.width],
                };
                std::uint8_t cube_case = 0;
                for (std::uint8_t i = 0; i < 8; ++i) {
                    if (c[i] >= h.iso_value) {
                        cube_case |= static_cast<std::uint8_t>(1U << i);
                    }
                }
                if (cube_case == 0 || cube_case == 255) continue;
                CellPacketV3 packet{};
                packet_set_bits(packet, 0, 12, x);
                packet_set_bits(packet, 12, 12, y);
                packet_set_bits(packet, 24, 12, z);
                packet_set_bits(packet, 36, 8, cube_case);
                packet_set_bits(packet, 44, 4, 3);
                for (std::uint8_t edge = 0; edge < 12; ++edge) {
                    const auto a = c[kEdgeCornersV2[edge][0]];
                    const auto b = c[kEdgeCornersV2[edge][1]];
                    const bool crosses = (a >= h.iso_value) != (b >= h.iso_value);
                    const auto t = crosses ? interpolate_q16(a, b, h.iso_value) : 0;
                    packet_set_bits(packet, 64 + 16 * edge, 16, t);
                }
                packets.push_back(packet);
            }
        }
    }
    return packets;
}

std::vector<TriangleToken> expand_cell_packets(
    const CellPacketV3* packets,
    std::size_t packet_count) {
    std::vector<TriangleToken> triangles;
    triangles.reserve(packet_count * 2);
    for (std::size_t packet_id = 0; packet_id < packet_count; ++packet_id) {
        const auto& packet = packets[packet_id];
        const auto x = static_cast<std::uint32_t>(packet_get_bits(packet, 0, 12));
        const auto y = static_cast<std::uint32_t>(packet_get_bits(packet, 12, 12));
        const auto z = static_cast<std::uint32_t>(packet_get_bits(packet, 24, 12));
        const auto cube_case = static_cast<std::uint8_t>(packet_get_bits(packet, 36, 8));
        const auto version = static_cast<std::uint8_t>(packet_get_bits(packet, 44, 4));
        if (version != 3 || cube_case == 0 || cube_case == 255) {
            throw std::runtime_error("invalid V3 cell packet");
        }
        for (std::uint8_t item = 0; item < 15; item += 3) {
            const signed char edge0 = kMcTriTable[cube_case][item];
            if (edge0 < 0) break;
            TriangleToken triangle{};
            for (std::uint8_t vertex = 0; vertex < 3; ++vertex) {
                const auto edge = static_cast<std::uint8_t>(kMcTriTable[cube_case][item + vertex]);
                const auto t = static_cast<std::uint16_t>(packet_get_bits(packet, 64 + 16 * edge, 16));
                triangle.vertex[vertex] = make_v3_vertex_token(x, y, z, edge, t);
            }
            triangles.push_back(triangle);
        }
    }
    return triangles;
}

IndexedMesh build_indexed_mesh(
    const VolumeBinHeader& header,
    const TriangleToken* triangles,
    std::size_t triangle_count) {
    IndexedMesh mesh;
    mesh.faces.reserve(triangle_count);
    std::unordered_map<std::uint64_t, std::uint32_t> point_index;
    point_index.reserve(triangle_count * 2);
    for (std::size_t triangle_id = 0; triangle_id < triangle_count; ++triangle_id) {
        std::array<std::uint32_t, 3> face{};
        for (int corner = 0; corner < 3; ++corner) {
            const std::uint64_t token = triangles[triangle_id].vertex[corner];
            const auto found = point_index.find(token);
            if (found == point_index.end()) {
                const auto index = static_cast<std::uint32_t>(mesh.points.size());
                point_index.emplace(token, index);
                mesh.points.push_back(decode_point(header, token));
                face[corner] = index;
            } else {
                face[corner] = found->second;
            }
        }
        if (face[0] != face[1] && face[1] != face[2] && face[0] != face[2]) {
            mesh.faces.push_back(face);
        }
    }

    mesh.normals.assign(mesh.points.size(), {0.0F, 0.0F, 0.0F});
    for (const auto& face : mesh.faces) {
        const auto& a = mesh.points[face[0]];
        const auto& b = mesh.points[face[1]];
        const auto& c = mesh.points[face[2]];
        const float ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
        const float vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
        const std::array<float, 3> normal = {
            uy * vz - uz * vy,
            uz * vx - ux * vz,
            ux * vy - uy * vx,
        };
        for (const auto index : face) {
            for (int component = 0; component < 3; ++component) {
                mesh.normals[index][component] += normal[component];
            }
        }
    }
    for (auto& normal : mesh.normals) {
        const float length = std::sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]);
        if (length > 0.0F) {
            for (float& component : normal) component /= length;
        }
    }
    return mesh;
}

void write_obj(const std::string& path, const IndexedMesh& mesh) {
    std::ofstream output(path);
    if (!output) throw std::runtime_error("cannot create OBJ: " + path);
    output << "# KV260 uint16 Marching Cubes reconstruction\n";
    for (const auto& point : mesh.points) {
        output << "v " << point[0] << ' ' << point[1] << ' ' << point[2] << '\n';
    }
    for (const auto& normal : mesh.normals) {
        output << "vn " << normal[0] << ' ' << normal[1] << ' ' << normal[2] << '\n';
    }
    for (const auto& face : mesh.faces) {
        output << "f";
        for (const auto index : face) output << ' ' << index + 1 << "//" << index + 1;
        output << '\n';
    }
}
