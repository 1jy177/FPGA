#include "mc_kv260.hpp"
#include "software.hpp"
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <cstdlib>
static ap_uint<128> input[8192];
static ap_uint<256> output[8192];

void check_volume(const VolumeData& v, unsigned offset, unsigned cap) {
    unsigned w=v.header.width,h=v.header.height,d=v.header.depth,rw=(w+7)/8;
    if (rw*h*d>8192 || (w-1)*(h-1)*(d-1)>8192) throw std::runtime_error("test too large");
    for(unsigned i=0;i<8192;++i) input[i]=0;
    for(unsigned z=0;z<d;++z)for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x)
        input[(z*h+y)*rw+x/8].range(x%8*16+15,x%8*16)=v.voxels[(z*h+y)*w+x];
    unsigned n,s,p,a;
    mc_kv260(input,output,w,h,d,rw,offset,v.header.iso_value,cap,n,s,p,a);
    auto expected=build_cell_packets_reference(v,0,d-1);
    if(a!=0x4b560002 || n!=std::min<size_t>(cap,expected.size()) ||
       s!=(expected.size()>cap?3u:0u) || p!=(w-1)*(h-1)*(d-1))
        throw std::runtime_error("status/count mismatch");
    for(unsigned i=0;i<n;++i){
        expected[i].word[0]+=uint64_t(offset)<<24;
        for(unsigned k=0;k<4;++k)if(output[i].range(k*64+63,k*64).to_uint64()!=expected[i].word[k]) {
            std::cerr<<"packet mismatch at "<<i<<" word "<<k<<"\n";
            throw std::runtime_error("packet mismatch");
        }
    }
}
void check(unsigned w,unsigned h,unsigned d,unsigned iso,unsigned offset,unsigned cap,unsigned seed, unsigned mode=0) {
    VolumeData v;v.header.width=w;v.header.height=h;v.header.depth=d;v.header.iso_value=iso;
    v.voxels.resize(w*h*d);
    for(unsigned i=0;i<v.voxels.size();++i) {
        seed=seed*1664525u+1013904223u;
        v.voxels[i]= mode==1 ? (i%2 ? iso : iso-1) :
                      mode==2 ? (i%2 ? 65535 : 0) : seed>>16;
    }
    check_volume(v,offset,cap);
}
int main(){
    check(33,9,5,32751,0,8192,42);
    check(33,9,5,32751,11,8192,42);
    check(33,9,5,32751,0,2,42);
    check(33,9,5,32751,0,0,42);
    check(17,5,4,0,0,8192,42);
    check(17,5,4,65535,0,8192,42);
    check(17,5,4,1,0,8192,42,1);
    check(17,5,4,65535,0,8192,42,1);
    check(17,5,4,32768,0,8192,42,2);
    check(17,5,4,1,0,8192,42,2);
    check(17,5,4,65535,0,8192,42,2);
    check(2,2,2,32768,4094,8192,19);
    check(4096,2,2,32768,0,8192,27);
    unsigned n,s,p,a;
    for(unsigned bad=0;bad<6;++bad) {
        mc_kv260(input,output,bad==0?1:(bad==5?4097:17),2,bad==4?66:2,bad==1?0:3,bad==2?4095:0,bad==3?65536:0,1,n,s,p,a);
        if(s!=1 || n!=0 || p!=0)throw std::runtime_error("invalid args accepted");
    }
    if(const char* data=std::getenv("MC_KV260_ROI")) {
        auto v=load_volume_bin(data);check_volume(v,17,8192);
        std::cout<<"PASS real anatomy ROI: "<<data<<"\n";
    }
    std::cout<<"PASS uint16 KV260: random/rounding/endpoints/unaligned/global-Z/overflow/zero-capacity/empty/max-width/invalid\n";
}
