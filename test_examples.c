// ISP Algorithm Example
// Demonstrates various features of CVAS parser

#include <stdint.h>

// This is outside CVAS region - will be ignored
void init_hardware(void) {
    // Hardware initialization
}

CVAS_START

// Simple BPC (Bad Pixel Correction) for 12-bit GRBG Bayer
// - 3x3 median-based
// - threshold = 256
// - boundary handling: clamp to edge (replicate)
// - if abs(curr - median) > threshold, replace with median

static int clamp_int(int value, int lo, int hi) {
    if (value < lo) return lo;
    if (value > hi) return hi;
    return value;
}

static int abs_int(int x) {
    return x < 0 ? -x : x;
}

static int median9(int v0, int v1, int v2, int v3, int v4, int v5, int v6, int v7, int v8) {
    int v[9] = { v0, v1, v2, v3, v4, v5, v6, v7, v8 };
    int i = 0;
    while (i < 9) {
        int j = i + 1;
        while (j < 9) {
            if (v[j] < v[i]) {
                int t = v[i];
                v[i] = v[j];
                v[j] = t;
            }
            j = j + 1;
        }
        i = i + 1;
    }
    return v[4];
}

static uint16_t get_pixel(const uint16_t *raw, int x, int y, int width, int height) {
    int cx = clamp_int(x, 0, width - 1);
    int cy = clamp_int(y, 0, height - 1);
    return raw[cy * width + cx];
}

// GRBG pattern helpers (top-left is G)
static int is_green(int x, int y) {
    return ((y & 1) == 0 && (x & 1) == 0) || ((y & 1) == 1 && (x & 1) == 1);
}

static int is_red(int x, int y) {
    return ((y & 1) == 0 && (x & 1) == 1);
}

static int is_blue(int x, int y) {
    return ((y & 1) == 1 && (x & 1) == 0);
}

static uint16_t bpc_pixel(const uint16_t *raw, int x, int y, int width, int height, int threshold) {
    int p0 = get_pixel(raw, x - 1, y - 1, width, height);
    int p1 = get_pixel(raw, x,     y - 1, width, height);
    int p2 = get_pixel(raw, x + 1, y - 1, width, height);
    int p3 = get_pixel(raw, x - 1, y,     width, height);
    int p4 = get_pixel(raw, x,     y,     width, height);
    int p5 = get_pixel(raw, x + 1, y,     width, height);
    int p6 = get_pixel(raw, x - 1, y + 1, width, height);
    int p7 = get_pixel(raw, x,     y + 1, width, height);
    int p8 = get_pixel(raw, x + 1, y + 1, width, height);

    int med = median9(p0, p1, p2, p3, p4, p5, p6, p7, p8);
    int diff = abs_int(p4 - med);
    int fixed = (diff > threshold) ? med : p4;

    // 12-bit clamp
    return (uint16_t)clamp_int(fixed, 0, 4095);
}

void bpc_grbg_3x3(
    const uint16_t *raw,
    uint16_t *out,
    int width,
    int height,
    int threshold
) {
    int x = 0;
    int y = 0;

    while (y < height) {
        x = 0;
        while (x < width) {
            // Pattern checks left here for clarity/future tuning
            if (is_green(x, y) || is_red(x, y) || is_blue(x, y)) {
                out[y * width + x] = bpc_pixel(raw, x, y, width, height, threshold);
            } else {
                out[y * width + x] = get_pixel(raw, x, y, width, height);
            }
            x = x + 1;
        }
        y = y + 1;
    }
}


// Outside CVAS region - will be ignored
int main(void) {
    int result = add_values(10, 20);
    return 0;
}
CVAS_END
