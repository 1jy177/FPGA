// Include the existing implementation in this translation unit so geometry,
// interpolation, table ordering and physical coordinates have one source.
#include "software.cpp"
#include <memory>
#include <limits>
#include <cstdio>
#include <unistd.h>

namespace {
thread_local std::string last_error;
struct Builder {
    VolumeBinHeader header;
    IndexedMesh mesh;
    std::unordered_map<uint64_t,uint32_t> index;
    size_t max_vertices, max_faces;
    unsigned current_z=0;
    bool have_z=false;
    size_t peak_cached=0;
    size_t vertices=0, faces=0;
    FILE *point_spool=nullptr, *face_spool=nullptr;
    ~Builder() {if(point_spool)std::fclose(point_spool);if(face_spool)std::fclose(face_spool);}
};
FILE* spool(const std::string& prefix) {
    std::string name=prefix+"-XXXXXX";
    std::vector<char> chars(name.begin(),name.end());chars.push_back(0);
    int fd=mkstemp(chars.data());
    if(fd<0)throw std::runtime_error("cannot create mesh spool on output storage");
    // The open descriptor owns this temporary file. Even a process crash
    // releases it; no multi-gigabyte orphan is left on the user's SSD.
    if(unlink(chars.data())!=0){close(fd);throw std::runtime_error("cannot unlink mesh spool");}
    FILE* f=fdopen(fd,"w+b");
    if(!f){close(fd);throw std::runtime_error("cannot open mesh spool");}
    return f;
}
void copy_spool(FILE* input,std::ofstream& output) {
    if(std::fflush(input)!=0 || std::fseek(input,0,SEEK_SET)!=0)
        throw std::runtime_error("mesh spool flush/seek failed (check free storage)");
    std::vector<char> block(1<<20);
    size_t n;
    while((n=std::fread(block.data(),1,block.size(),input))!=0) {
        output.write(block.data(),n);
        if(!output)throw std::runtime_error("mesh file write failed (check free storage)");
    }
    if(std::ferror(input))throw std::runtime_error("mesh spool read failed");
}
template<class F> int guard(F f) {
    try { f(); last_error.clear(); return 0; }
    catch(const std::exception& e) {last_error=e.what(); return -1;}
}
}
extern "C" {
const char* mc_error() {return last_error.c_str();}
void* mc_create(const void* header, size_t max_vertices, size_t max_faces, const char* prefix) {
    try {
        std::unique_ptr<Builder> b(new Builder);
        std::memcpy(&b->header,header,sizeof(VolumeBinHeader));
        b->max_vertices=max_vertices; b->max_faces=max_faces;
        b->index.reserve(std::min<size_t>(max_vertices,262144));
        if(prefix) {
            b->point_spool=spool(std::string(prefix)+".points");
            b->face_spool=spool(std::string(prefix)+".faces");
        } else {
            b->mesh.points.reserve(std::min<size_t>(max_vertices,262144));
            b->mesh.faces.reserve(std::min<size_t>(max_faces,524288));
        }
        return b.release();
    } catch(const std::exception& e) {last_error=e.what(); return nullptr;}
}
void mc_destroy(void* handle) {delete static_cast<Builder*>(handle);}
int mc_append(void* handle, const void* data, size_t count) {
    return guard([&] {
        Builder& b=*static_cast<Builder*>(handle);
        const CellPacketV3* packets=static_cast<const CellPacketV3*>(data);
        // Expand directly from the table: no heap vector per active cell.
        for(size_t i=0;i<count;++i) {
            const auto& packet=packets[i];
            unsigned x=packet_get_bits(packet,0,12),y=packet_get_bits(packet,12,12),z=packet_get_bits(packet,24,12);
            unsigned code=packet_get_bits(packet,36,8);
            if(packet_get_bits(packet,44,4)!=3 || code==0 || code==255)
                throw std::runtime_error("invalid V3 cell packet");
            if(x+1>=b.header.width || y+1>=b.header.height || z+1>=b.header.depth)
                throw std::runtime_error("packet outside volume bounds");
            // Packets arrive in deterministic ascending Z order. Only the
            // current and next grid planes can be reused by future cells.
            // Evict obsolete edge keys while retaining the final indexed
            // geometry. This avoids a whole-animal unordered_map on ARM.
            if(b.have_z && z<b.current_z)throw std::runtime_error("packets must be ordered by Z");
            if(!b.have_z || z>b.current_z) {
                for(auto it=b.index.begin();it!=b.index.end();) {
                    if(((it->first>>24)&0xfff)<z)it=b.index.erase(it);
                    else ++it;
                }
                b.current_z=z;b.have_z=true;
            }
            for(unsigned item=0;item<15 && kMcTriTable[code][item]>=0;item+=3) {
                std::array<uint32_t,3> face;
                for(unsigned j=0;j<3;++j) {
                    unsigned edge=kMcTriTable[code][item+j];
                    auto token=make_v3_vertex_token(x,y,z,edge,packet_get_bits(packet,64+16*edge,16));
                    auto it=b.index.find(token);
                    if(it==b.index.end()) {
                        if(b.vertices>=b.max_vertices) throw std::runtime_error("vertex budget exceeded; increase the explicit output budget or use an ROI");
                        uint32_t idx=b.vertices;
                        b.index.emplace(token,idx);
                        auto point=decode_point(b.header,token);
                        if(b.point_spool) {
                            if(std::fwrite(point.data(),12,1,b.point_spool)!=1)
                                throw std::runtime_error("point spool write failed (check free storage)");
                        } else b.mesh.points.push_back(point);
                        ++b.vertices;
                        face[j]=idx;
                    } else face[j]=it->second;
                }
                if(face[0]==face[1] || face[0]==face[2] || face[1]==face[2]) continue;
                if(b.faces>=b.max_faces) throw std::runtime_error("triangle budget exceeded");
                if(b.face_spool) {
                    if(std::fwrite(face.data(),12,1,b.face_spool)!=1)
                        throw std::runtime_error("face spool write failed (check free storage)");
                } else b.mesh.faces.push_back(face);
                ++b.faces;
            }
            b.peak_cached=std::max(b.peak_cached,b.index.size());
        }
    });
}
size_t mc_vertices(void* h) {return static_cast<Builder*>(h)->vertices;}
size_t mc_faces(void* h) {return static_cast<Builder*>(h)->faces;}
size_t mc_cache_peak(void* h) {return static_cast<Builder*>(h)->peak_cached;}
int mc_copy(void* h, void* points, void* faces) {
    return guard([&] {
        const auto& b=*static_cast<Builder*>(h);
        if(b.point_spool)throw std::runtime_error("streamed mesh must be exported to its output file");
        const auto& m=b.mesh;
        if(!m.points.empty()) std::memcpy(points,m.points.data(),m.points.size()*12);
        if(!m.faces.empty()) std::memcpy(faces,m.faces.data(),m.faces.size()*12);
    });
}
int mc_write(void* h, const char* path) {
    return guard([&] {
        auto& b=*static_cast<Builder*>(h);
        const auto& m=b.mesh;
        std::ofstream output(path,std::ios::binary);
        if(!output)throw std::runtime_error("cannot create mesh file");
        if(b.point_spool) {copy_spool(b.point_spool,output);copy_spool(b.face_spool,output);}
        else {
            if(!m.points.empty())output.write(reinterpret_cast<const char*>(m.points.data()),m.points.size()*12);
            if(!m.faces.empty())output.write(reinterpret_cast<const char*>(m.faces.data()),m.faces.size()*12);
        }
        output.flush();
        if(!output)throw std::runtime_error("mesh file write failed (check free storage)");
    });
}
// CPU backend is explicit and is never used as a silent hardware fallback.
int mc_reference(const void* header, const unsigned char* payload, unsigned z_offset,
                 void* out, size_t capacity, size_t* count) {
    return guard([&] {
        VolumeData v;
        std::memcpy(&v.header,header,sizeof(VolumeBinHeader));
        size_t size=size_t(v.header.width)*v.header.height*v.header.depth;
        v.voxels.resize(size);
        std::memcpy(v.voxels.data(),payload,size*2);
        auto packets=build_cell_packets_reference(v,0,v.header.depth-1);
        if(packets.size()>capacity) throw std::runtime_error("reference capacity exceeded");
        for(auto& packet:packets) packet.word[0]+=uint64_t(z_offset)<<24;
        if(!packets.empty()) std::memcpy(out,packets.data(),packets.size()*32);
        *count=packets.size();
    });
}
}
