#include "mc_kv260.hpp"
#include <hls_stream.h>

namespace {
const unsigned MAX_WIDTH = 4096;
struct Item { ap_uint<256> packet; bool last; };
struct Stats { unsigned processed, status; };

ap_uint<16> interp(ap_uint<16> a, ap_uint<16> b, ap_uint<16> iso) {
#pragma HLS INLINE
    const bool cross = (a >= iso) != (b >= iso);
    // Exact round-to-nearest UNORM16. The constrained numerator has a
    // 16-bit quotient, so only 16 restoring stages are necessary. All
    // stages and all 12 edges are unrolled; the SCAN pipeline schedules
    // them across cycles, without a shared divider or reciprocal ROM.
    const ap_uint<16> d = cross ? ap_uint<16>(a > b ? a-b : b-a) : ap_uint<16>(1);
    const ap_uint<16> distance = cross ? ap_uint<16>(iso > a ? iso-a : a-iso) : ap_uint<16>(0);
    const ap_uint<32> n = (ap_uint<32>(distance) << 16) - distance + (d >> 1);
    ap_uint<17> rem = n.range(31,16);
    ap_uint<16> q = 0;
    DIVIDE: for (int bit=15; bit>=0; --bit) {
#pragma HLS UNROLL
        rem = (rem << 1) | ap_uint<17>(n[bit]);
        if (rem >= d) { rem -= d; q[bit] = 1; }
    }
    return cross ? q : ap_uint<16>(0);
}
void load_row(const ap_uint<128>* volume, ap_uint<16> row[MAX_WIDTH],
              unsigned base, unsigned words) {
#pragma HLS INLINE off
#pragma HLS ARRAY_PARTITION variable=row cyclic factor=8
    LOAD: for (unsigned i=0; i<words; ++i) {
#pragma HLS PIPELINE II=1
        ap_uint<128> v=volume[base+i];
        for(unsigned b=0;b<8;++b) {
#pragma HLS UNROLL
            row[i*8+b]=v.range(b*16+15,b*16);
        }
    }
}
void scan(const ap_uint<128>* volume, hls::stream<Item>& stream,
          hls::stream<Stats>& stats, unsigned width, unsigned height,
          unsigned depth, unsigned row_words, unsigned z_offset, unsigned iso) {
#pragma HLS INLINE off
    Stats s={0,0};
    if(width<2 || width>MAX_WIDTH || height<2 || height>4096 || depth<2 ||
       depth>65 || z_offset>4094 || z_offset+depth>4096 ||
       row_words!=(width+7)/8 || iso>65535) {
        s.status=1;
    } else {
        ap_uint<16> rows[4][MAX_WIDTH];
#pragma HLS ARRAY_PARTITION variable=rows complete dim=1
#pragma HLS ARRAY_PARTITION variable=rows cyclic factor=8 dim=2
        const unsigned char ea[12]={0,1,3,0,4,5,7,4,0,1,2,3};
        const unsigned char eb[12]={1,2,2,3,5,6,6,7,4,5,6,7};
#pragma HLS ARRAY_PARTITION variable=ea complete
#pragma HLS ARRAY_PARTITION variable=eb complete
        for(unsigned z=0;z+1<depth;++z) {
            for(unsigned y=0;y+1<height;++y) {
                unsigned base=(z*height+y)*row_words;
                load_row(volume,rows[0],base,row_words);
                load_row(volume,rows[1],base+row_words,row_words);
                load_row(volume,rows[2],base+height*row_words,row_words);
                load_row(volume,rows[3],base+(height+1)*row_words,row_words);
                ap_uint<16> left0=rows[0][0],left1=rows[1][0];
                ap_uint<16> left2=rows[2][0],left3=rows[3][0];
                SCAN: for(unsigned x=1;x<width;++x) {
#pragma HLS PIPELINE II=1
                    ap_uint<16> r0=rows[0][x],r1=rows[1][x],r2=rows[2][x],r3=rows[3][x];
                    ap_uint<16> c[8]={left0,r0,r1,left1,left2,r2,r3,left3};
#pragma HLS ARRAY_PARTITION variable=c complete
                    ap_uint<8> code=0;
                    for(unsigned i=0;i<8;++i) {
#pragma HLS UNROLL
                        code[i]=c[i]>=iso;
                    }
                    if(code!=0 && code!=255) {
                        Item item; item.packet=0; item.last=false;
                        item.packet.range(11,0)=x-1;
                        item.packet.range(23,12)=y;
                        item.packet.range(35,24)=z+z_offset;
                        item.packet.range(43,36)=code;
                        item.packet.range(47,44)=3;
                        for(unsigned e=0;e<12;++e) {
#pragma HLS UNROLL
                            item.packet.range(64+16*e+15,64+16*e)=interp(c[ea[e]],c[eb[e]],iso);
                        }
                        stream.write(item);
                    }
                    left0=r0;left1=r1;left2=r2;left3=r3;
                }
            }
        }
        s.processed=(width-1)*(height-1)*(depth-1);
    }
    Item end;end.packet=0;end.last=true;stream.write(end);stats.write(s);
}
void write_packets(ap_uint<256>* packets,hls::stream<Item>& stream,hls::stream<Stats>& stats,
                   unsigned capacity,unsigned& count,unsigned& status,unsigned& processed) {
#pragma HLS INLINE off
    unsigned n=0;bool overflow=false,done=false;
    WRITE: while(!done) {
#pragma HLS PIPELINE II=1
        Item item=stream.read();
        if(item.last) done=true;
        else if(n<capacity) packets[n++]=item.packet;
        else overflow=true;
    }
    Stats s=stats.read();count=n;processed=s.processed;status=s.status?s.status:(overflow?3:0);
}
}
extern "C" void mc_kv260(const ap_uint<128>* volume, ap_uint<256>* packets,
    unsigned width,unsigned height,unsigned depth,unsigned row_words,
    unsigned z_offset,unsigned iso_value,unsigned capacity,
    unsigned& count,unsigned& status,unsigned& processed,unsigned& abi) {
#pragma HLS INTERFACE m_axi port=volume offset=slave bundle=input_mem depth=8192 max_read_burst_length=128 num_read_outstanding=32
#pragma HLS INTERFACE m_axi port=packets offset=slave bundle=output_mem depth=8192 max_write_burst_length=64 num_write_outstanding=8
#pragma HLS INTERFACE s_axilite port=volume bundle=control offset=0x10
#pragma HLS INTERFACE s_axilite port=packets bundle=control offset=0x1c
#pragma HLS INTERFACE s_axilite port=width bundle=control offset=0x28
#pragma HLS INTERFACE s_axilite port=height bundle=control offset=0x30
#pragma HLS INTERFACE s_axilite port=depth bundle=control offset=0x38
#pragma HLS INTERFACE s_axilite port=row_words bundle=control offset=0x40
#pragma HLS INTERFACE s_axilite port=z_offset bundle=control offset=0x48
#pragma HLS INTERFACE s_axilite port=iso_value bundle=control offset=0x50
#pragma HLS INTERFACE s_axilite port=capacity bundle=control offset=0x58
#pragma HLS INTERFACE s_axilite port=count bundle=control offset=0x60
#pragma HLS INTERFACE s_axilite port=status bundle=control offset=0x70
#pragma HLS INTERFACE s_axilite port=processed bundle=control offset=0x80
#pragma HLS INTERFACE s_axilite port=abi bundle=control offset=0x90
#pragma HLS INTERFACE s_axilite port=return bundle=control
#pragma HLS DATAFLOW
    hls::stream<Item> stream;
    hls::stream<Stats> stats;
#pragma HLS STREAM variable=stream depth=256
#pragma HLS STREAM variable=stats depth=2
    abi=0x4b560002;
    scan(volume,stream,stats,width,height,depth,row_words,z_offset,iso_value);
    write_packets(packets,stream,stats,capacity,count,status,processed);
}
