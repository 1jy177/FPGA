#ifndef MC_SOFTWARE_HPP
#define MC_SOFTWARE_HPP

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "mc_types.hpp"

struct VolumeData {
    VolumeBinHeader header{};
    std::vector<std::uint16_t> voxels;
};

struct IndexedMesh {
    std::vector<std::array<float, 3>> points;
    std::vector<std::array<std::uint32_t, 3>> faces;
    std::vector<std::array<float, 3>> normals;
};

std::uint32_t crc32(const std::uint8_t* data, std::size_t size);
VolumeData load_volume_bin(const std::string& path);
void save_volume_bin(const std::string& path, VolumeData volume);
IndexedMesh build_indexed_mesh(
    const VolumeBinHeader& header,
    const TriangleToken* triangles,
    std::size_t triangle_count);
std::vector<CellPacketV3> build_cell_packets_reference(
    const VolumeData& volume,
    std::uint32_t z_begin,
    std::uint32_t z_end);
std::vector<TriangleToken> expand_cell_packets(
    const CellPacketV3* packets,
    std::size_t packet_count);
void write_obj(const std::string& path, const IndexedMesh& mesh);

#endif
