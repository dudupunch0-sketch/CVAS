// ISP Algorithm Example
// Demonstrates various features of CVAS parser

#include <stdint.h>

// This is outside CVAS region - will be ignored
void init_hardware(void) {
    // Hardware initialization
}

CVAS_START

// Deep datapath BPC (Bad Pixel Correction) for 12-bit GRBG Bayer
// - 3x3 median + directional predictor fusion
// - adaptive threshold from local activity
// - boundary handling: clamp to edge (replicate)
// - multi-stage refinement to create a deeper datapath/call chain

static int clamp_int(int value, int lo, int hi) {
    if (value < lo) return lo;
    if (value > hi) return hi;
    return value;
}

static int abs_int(int x) {
    return x < 0 ? -x : x;
}

static int sum3(int a, int b, int c) {
    return a + b + c;
}

static int avg3_round(int a, int b, int c) {
    int total = sum3(a, b, c);
    return (total + 1) / 3;
}

static int directional_energy(int a, int b, int c) {
    int left_grad = abs_int(b - a);
    int right_grad = abs_int(c - b);
    int span_grad = abs_int(c - a);
    return left_grad + right_grad + span_grad;
}

static int weighted_blend(int a, int b, int wa, int wb, int shift) {
    int bias = 1 << (shift - 1);
    int mixed = a * wa + b * wb + bias;
    return mixed >> shift;
}

static int select_directional_predictor(
    int pred_h,
    int pred_v,
    int pred_d0,
    int pred_d1,
    int score_h,
    int score_v,
    int score_d0,
    int score_d1
) {
    int best_pred = pred_h;
    int best_score = score_h;

    if (score_v < best_score) {
        best_score = score_v;
        best_pred = pred_v;
    }
    if (score_d0 < best_score) {
        best_score = score_d0;
        best_pred = pred_d0;
    }
    if (score_d1 < best_score) {
        best_pred = pred_d1;
    }
    return best_pred;
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

static int edge_aware_predict(
    int p0,
    int p1,
    int p2,
    int p3,
    int p4,
    int p5,
    int p6,
    int p7,
    int p8
) {
    int pred_h = avg3_round(p3, p4, p5);
    int pred_v = avg3_round(p1, p4, p7);
    int pred_d0 = avg3_round(p0, p4, p8);
    int pred_d1 = avg3_round(p2, p4, p6);

    int score_h = directional_energy(p3, p4, p5);
    int score_v = directional_energy(p1, p4, p7);
    int score_d0 = directional_energy(p0, p4, p8);
    int score_d1 = directional_energy(p2, p4, p6);

    return select_directional_predictor(
        pred_h,
        pred_v,
        pred_d0,
        pred_d1,
        score_h,
        score_v,
        score_d0,
        score_d1
    );
}

static int ring_activity(
    int p0,
    int p1,
    int p2,
    int p3,
    int p4,
    int p5,
    int p6,
    int p7,
    int p8
) {
    int cross_sum = sum3(p1, p4, p7) + p3 + p5;
    int diag_sum = sum3(p0, p4, p8) + p2 + p6;
    int outer_sum = sum3(p0, p1, p2) + sum3(p6, p7, p8) + p3 + p5;
    int axis_delta = abs_int(cross_sum - diag_sum);
    int ring_delta = abs_int(outer_sum - (cross_sum + diag_sum));
    return axis_delta + ring_delta;
}

static int adaptive_threshold(
    int center,
    int median,
    int edge_pred,
    int activity,
    int base_threshold
) {
    int diff_med = abs_int(center - median);
    int diff_edge = abs_int(center - edge_pred);
    int local = base_threshold + (diff_med >> 1) + (diff_edge >> 2) + (activity >> 3);

    if (local < 64) {
        return 64;
    }
    if (local > 1023) {
        return 1023;
    }
    return local;
}

static int refine_candidate(int center, int median, int edge_pred, int threshold) {
    int first = weighted_blend(median, edge_pred, 3, 5, 3);
    int second = weighted_blend(first, center, 7, 1, 3);
    int diff_first = abs_int(center - first);
    int diff_second = abs_int(center - second);

    if (diff_first > threshold && diff_second > (threshold >> 1)) {
        return second;
    }
    if (diff_first > threshold) {
        return first;
    }
    return center;
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
    int edge_pred = edge_aware_predict(p0, p1, p2, p3, p4, p5, p6, p7, p8);
    int activity = ring_activity(p0, p1, p2, p3, p4, p5, p6, p7, p8);
    int fused_pred = weighted_blend(med, edge_pred, 3, 5, 3);
    int local_threshold = adaptive_threshold(p4, med, fused_pred, activity, threshold);
    int refined = refine_candidate(p4, med, fused_pred, local_threshold);
    int residual = abs_int(p4 - refined);
    int fixed = (residual > (local_threshold >> 2)) ? refined : p4;

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
    int safe_threshold = clamp_int(threshold, 32, 1023);
    int x = 0;
    int y = 0;

    while (y < height) {
        x = 0;
        while (x < width) {
            int index = y * width + x;

            // Pattern checks left here for clarity/future tuning
            if (is_green(x, y) || is_red(x, y) || is_blue(x, y)) {
                out[index] = bpc_pixel(raw, x, y, width, height, safe_threshold);
            } else {
                out[index] = get_pixel(raw, x, y, width, height);
            }
            x = x + 1;
        }
        y = y + 1;
    }
}

CVAS_END

// Outside CVAS region - will be ignored
int main(void) {
    int result = add_values(10, 20);
    return 0;
}
