#pragma once
#include <ap_int.h>
extern "C" void mc_kv260(const ap_uint<128>* volume, ap_uint<256>* packets,
    unsigned width, unsigned height, unsigned depth, unsigned row_words,
    unsigned z_offset, unsigned iso_value, unsigned capacity,
    unsigned& count, unsigned& status, unsigned& processed, unsigned& abi);
