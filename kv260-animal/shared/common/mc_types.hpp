#ifndef MC_TYPES_HPP
#define MC_TYPES_HPP

#include <cstdint>

constexpr std::uint32_t kVolumeMagic = 0x344C5856U; // "VXL4" little-endian
constexpr std::uint16_t kVolumeVersion = 1;
constexpr std::uint16_t kVolumeHeaderBytes = 128;
constexpr std::uint32_t kMaxDimension = 4096;
constexpr std::uint32_t kMaxTrianglesPerCell = 5;

#pragma pack(push, 1)
struct VolumeBinHeader {
    std::uint32_t magic;
    std::uint16_t version;
    std::uint16_t header_bytes;
    std::uint32_t width;
    std::uint32_t height;
    std::uint32_t depth;
    std::uint16_t data_type;
    std::uint16_t tissue_id;
    float spacing[3];
    float origin[3];
    float direction[9];
    std::uint16_t iso_value;
    std::uint16_t flags;
    std::uint64_t payload_bytes;
    std::uint32_t payload_crc32;
    std::uint8_t reserved[28];
};

struct TriangleToken {
    std::uint64_t vertex[3];
};

struct KernelStats {
    std::uint32_t triangle_count;
    std::uint32_t active_cell_count;
    std::uint32_t processed_cell_count;
    std::uint32_t status;
};

struct CellPacketV3 {
    std::uint64_t word[4];
};

struct KernelStatsV2 {
    std::uint32_t packet_count;
    std::uint32_t active_cell_count;
    std::uint32_t processed_cell_count;
    std::uint32_t status;
};
#pragma pack(pop)

static_assert(sizeof(VolumeBinHeader) == kVolumeHeaderBytes, "volume header must be 128 bytes");
static_assert(sizeof(TriangleToken) == 24, "triangle token must be 24 bytes");
static_assert(sizeof(KernelStats) == 16, "kernel stats must be 16 bytes");
static_assert(sizeof(CellPacketV3) == 32, "V3 cell packet must be 32 bytes");
static_assert(sizeof(KernelStatsV2) == 16, "V2 kernel stats must be 16 bytes");

enum KernelStatus : std::uint32_t {
    kStatusOk = 0,
    kStatusBadDimensions = 1,
    kStatusBadBlock = 2,
    kStatusOutputOverflow = 3,
};

#endif
