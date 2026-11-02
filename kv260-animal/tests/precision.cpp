// Exercise the actual synthesizable interpolation function, independently of
// the software mesh reference. Compile using Vitis HLS' ap_int include path.
#include "../hardware/hls/mc_kv260.cpp"
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <algorithm>

int main() {
    double max_error=0, sum_error=0;
    unsigned long long checked=0;
    for(unsigned d=1;d<=65535;++d) {
        unsigned distances[6]={0,1,d/3,d/2,d-1,d};
        for(auto distance:distances)for(unsigned reverse=0;reverse<2;++reverse) {
            unsigned a=reverse?d:0,b=reverse?0:d,iso=reverse?d-distance:distance;
            bool crosses=(a>=iso)!=(b>=iso);
            unsigned q=interp(a,b,iso).to_uint();
            unsigned gold=crosses?(distance*65535U+d/2)/d:0;
            if(q!=gold)throw std::runtime_error("exact restoring divider mismatch");
            if(crosses) {
                double error=std::abs(q/65535.0-distance/double(d));
                max_error=std::max(max_error,error);sum_error+=error;++checked;
            }
        }
    }
    if(max_error>0.5/65535.0+1e-14)throw std::runtime_error("UNORM16 error bound failed");
    std::cout<<"{\"passed\":true,\"all_denominators\":65535,\"crossing_edges_checked\":"<<checked
             <<",\"max_edge_error_voxels\":"<<max_error
             <<",\"mean_edge_error_voxels\":"<<sum_error/checked
             <<",\"theoretical_bound_voxels\":"<<0.5/65535.0<<"}\n";
}
