// Simple BPC C-model used by CVAS sample output.
// The whole model is inside the CVAS region.  Data flows through a
// sequential pixel pipeline, with two logical fork/join sections where
// independent branch calculations can be viewed as parallel datapaths.

CVAS_START

static int bpc_abs(int value) {
    if (value < 0) {
        return -value;
    }
    return value;
}

static int bpc_clamp(int value, int lo, int hi) {
    if (value < lo) {
        return lo;
    }
    if (value > hi) {
        return hi;
    }
    return value;
}

static int bpc_load_pixel(const int *raw, int x, int y, int width, int height) {
    int safe_x = bpc_clamp(x, 0, width - 1);
    int safe_y = bpc_clamp(y, 0, height - 1);
    int index = safe_y * width + safe_x;
    return raw[index];
}

static int bpc_average2(int a, int b) {
    return (a + b + 1) >> 1;
}

static int bpc_pair_score(int center, int a, int b) {
    int left_delta = bpc_abs(center - a);
    int right_delta = bpc_abs(center - b);
    int pair_delta = bpc_abs(a - b);
    return left_delta + right_delta + pair_delta;
}

static int bpc_parallel_horizontal_predict(int left, int right) {
    return bpc_average2(left, right);
}

static int bpc_parallel_horizontal_score(int center, int left, int right) {
    return bpc_pair_score(center, left, right);
}

static int bpc_parallel_vertical_predict(int up, int down) {
    return bpc_average2(up, down);
}

static int bpc_parallel_vertical_score(int center, int up, int down) {
    return bpc_pair_score(center, up, down);
}

static int bpc_parallel_diag_predict(int up_left, int down_right) {
    return bpc_average2(up_left, down_right);
}

static int bpc_parallel_diag_score(int center, int up_left, int down_right) {
    return bpc_pair_score(center, up_left, down_right);
}

static int bpc_join_parallel_predictions(
    int h_pred,
    int v_pred,
    int d_pred,
    int h_score,
    int v_score,
    int d_score
) {
    int best_pred = h_pred;
    int best_score = h_score;

    if (v_score < best_score) {
        best_pred = v_pred;
        best_score = v_score;
    }
    if (d_score < best_score) {
        best_pred = d_pred;
    }
    return best_pred;
}

static int bpc_parallel_bright_defect(int center, int predicted, int threshold) {
    int delta = center - predicted;
    if (delta > threshold) {
        return 1;
    }
    return 0;
}

static int bpc_parallel_dark_defect(int center, int predicted, int threshold) {
    int delta = predicted - center;
    if (delta > threshold) {
        return 1;
    }
    return 0;
}

static int bpc_parallel_texture_gate(int h_score, int v_score, int d_score, int threshold) {
    int texture = h_score + v_score + d_score;
    int flat_limit = threshold + (threshold >> 1);
    if (texture < flat_limit) {
        return 1;
    }
    return 0;
}

static int bpc_join_defect_flags(int bright_flag, int dark_flag, int texture_gate) {
    int polarity_hit = bright_flag || dark_flag;
    if (polarity_hit && texture_gate) {
        return 1;
    }
    return 0;
}

static int bpc_select_output(int center, int predicted, int defect_flag) {
    if (defect_flag) {
        return predicted;
    }
    return center;
}

static int simple_bpc_pixel(
    const int *raw,
    int x,
    int y,
    int width,
    int height,
    int threshold
) {
    // Stage 1: sequential input gather.
    int center = bpc_load_pixel(raw, x, y, width, height);
    int left = bpc_load_pixel(raw, x - 1, y, width, height);
    int right = bpc_load_pixel(raw, x + 1, y, width, height);
    int up = bpc_load_pixel(raw, x, y - 1, width, height);
    int down = bpc_load_pixel(raw, x, y + 1, width, height);
    int up_left = bpc_load_pixel(raw, x - 1, y - 1, width, height);
    int down_right = bpc_load_pixel(raw, x + 1, y + 1, width, height);

    // Stage 2: logical parallel predictor lanes.  These lanes only read the
    // gathered neighborhood and are joined by bpc_join_parallel_predictions().
    int h_pred = bpc_parallel_horizontal_predict(left, right);
    int h_score = bpc_parallel_horizontal_score(center, left, right);
    int v_pred = bpc_parallel_vertical_predict(up, down);
    int v_score = bpc_parallel_vertical_score(center, up, down);
    int d_pred = bpc_parallel_diag_predict(up_left, down_right);
    int d_score = bpc_parallel_diag_score(center, up_left, down_right);
    int predicted = bpc_join_parallel_predictions(
        h_pred,
        v_pred,
        d_pred,
        h_score,
        v_score,
        d_score
    );

    // Stage 3: second logical parallel section for independent defect tests.
    int bright_flag = bpc_parallel_bright_defect(center, predicted, threshold);
    int dark_flag = bpc_parallel_dark_defect(center, predicted, threshold);
    int texture_gate = bpc_parallel_texture_gate(h_score, v_score, d_score, threshold);
    int defect_flag = bpc_join_defect_flags(bright_flag, dark_flag, texture_gate);

    // Stage 4: sequential select and output clamp.
    int selected = bpc_select_output(center, predicted, defect_flag);
    return bpc_clamp(selected, 0, 4095);
}

void simple_bpc_frame(
    const int *raw,
    int *out,
    int width,
    int height,
    int threshold
) {
    int safe_threshold = bpc_clamp(threshold, 4, 1024);
    int y = 0;

    while (y < height) {
        int x = 0;
        while (x < width) {
            int index = y * width + x;
            int corrected = simple_bpc_pixel(raw, x, y, width, height, safe_threshold);
            out[index] = corrected;
            x = x + 1;
        }
        y = y + 1;
    }
}

CVAS_END
