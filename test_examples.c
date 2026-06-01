// Expanded BPC C-model used by CVAS sample output.
// The model stays C-compatible so the default fast-mode sample remains stable.
// C++ grammar stress coverage lives in tests/fixtures/syntax/cpp_syntax_coverage.cpp.

#define BPC_MIN(a, b) ((a) < (b) ? (a) : (b))
#define BPC_MAX(a, b) ((a) > (b) ? (a) : (b))
#define BPC_TRACE(fmt, ...) printf(fmt, __VA_ARGS__)
#define NULL ((void *)0)

CVAS_START

typedef unsigned short bpc_sample_t;
typedef unsigned int bpc_flags_t;
typedef struct bpc_file FILE;

extern int printf(const char *fmt, ...);
extern int fprintf(FILE *stream, const char *fmt, ...);
extern int sprintf(char *buffer, const char *fmt, ...);
extern FILE *fopen(const char *path, const char *mode);
extern int fclose(FILE *stream);

enum {
    BPC_KERNEL_SIZE = 3,
    BPC_KERNEL_TAPS = 9,
    BPC_MAX_VALUE = 4095,
    BPC_MIN_THRESHOLD = 4,
    BPC_MAX_THRESHOLD = 1024,
    BPC_MASK_VALID = (1 << 0),
    BPC_MASK_BORDER = (1 << 1),
    BPC_MASK_TEXTURE = (1 << 2),
    BPC_MASK_DEFECT = (1 << 3),
    BPC_MASK_OUTPUT = (1 << 4)
};

enum bpc_pixel_state {
    BPC_PIXEL_CLEAN = 0,
    BPC_PIXEL_BRIGHT = 1,
    BPC_PIXEL_DARK = 2,
    BPC_PIXEL_TEXTURE = 3,
    BPC_PIXEL_BORDER = 4
};

struct bpc_debug_record {
    char text[96];
    int samples[4];
    bpc_flags_t flags[2];
};

typedef struct {
    int x;
    int y;
    int width;
    int height;
    int index;
    int border;
} bpc_coord_t;

typedef struct {
    bpc_sample_t center;
    bpc_sample_t taps[BPC_KERNEL_TAPS];
    bpc_sample_t matrix[BPC_KERNEL_SIZE][BPC_KERNEL_SIZE];
} bpc_window_t;

typedef struct {
    int threshold;
    int high_threshold;
    bpc_flags_t flags;
} bpc_config_t;

typedef struct {
    bpc_coord_t coord;
    bpc_window_t window;
    bpc_config_t config;
    bpc_flags_t flags;
} bpc_stage1_t;

typedef struct {
    int horizontal;
    int vertical;
    int diagonal;
    int activity;
    bpc_flags_t flags;
} bpc_stage2_t;

typedef struct {
    int horizontal;
    int vertical;
    int diagonal;
    int median;
    int selected;
} bpc_stage3_t;

typedef struct {
    int bright;
    int dark;
    int texture;
    int edge;
    enum bpc_pixel_state state;
} bpc_stage4_t;

typedef struct {
    int replacement;
    int confidence;
    bpc_flags_t packed_flags;
    int clamped;
} bpc_stage5_t;

typedef struct {
    int value;
    bpc_flags_t status;
    int debug_len;
    struct bpc_debug_record debug;
} bpc_stage6_t;

static int bpc_abs(int value) {
    if (value < 0) {
        return -value;
    }
    return value;
}

static int bpc_clamp(int value, int lo, int hi) {
    return value < lo ? lo : (value > hi ? hi : value);
}

static bpc_sample_t bpc_load_pixel(const int *raw, int x, int y, int width, int height) {
    int safe_x = bpc_clamp(x, 0, width - 1);
    int safe_y = bpc_clamp(y, 0, height - 1);
    int index = safe_y * width + safe_x;
    return (bpc_sample_t)bpc_clamp(raw[index], 0, BPC_MAX_VALUE);
}

static bpc_coord_t bpc_stage1_coord_lane(int x, int y, int width, int height) {
    bpc_coord_t coord;
    coord.x = bpc_clamp(x, 0, width - 1);
    coord.y = bpc_clamp(y, 0, height - 1);
    coord.width = width;
    coord.height = height;
    coord.index = coord.y * width + coord.x;
    coord.border = coord.x == 0 || coord.y == 0 || coord.x == width - 1 || coord.y == height - 1;
    return coord;
}

static bpc_window_t bpc_stage1_window_lane(
    const int *raw,
    bpc_coord_t coord,
    int (*window)[BPC_KERNEL_SIZE]
) {
    bpc_window_t result;
    int offsets[BPC_KERNEL_SIZE] = {-1, 0, 1};
    int tap = 0;

    for (int yy = 0; yy < BPC_KERNEL_SIZE; yy = yy + 1) {
        for (int xx = 0; xx < BPC_KERNEL_SIZE; xx = xx + 1) {
            bpc_sample_t value = bpc_load_pixel(
                raw,
                coord.x + offsets[xx],
                coord.y + offsets[yy],
                coord.width,
                coord.height
            );
            window[yy][xx] = value;
            result.matrix[yy][xx] = value;
            result.taps[tap] = value;
            tap = tap + 1;
        }
    }
    result.center = result.matrix[1][1];
    return result;
}

static bpc_config_t bpc_stage1_threshold_lane(int threshold) {
    bpc_config_t config;
    config.threshold = bpc_clamp(threshold, BPC_MIN_THRESHOLD, BPC_MAX_THRESHOLD);
    config.high_threshold = config.threshold + (config.threshold >> 1);
    config.flags = BPC_MASK_VALID;
    return config;
}

static bpc_flags_t bpc_stage1_border_lane(bpc_coord_t coord) {
    bpc_flags_t flags = BPC_MASK_VALID;
    int countdown = BPC_KERNEL_SIZE;
    do {
        if (coord.border) {
            flags |= BPC_MASK_BORDER;
        }
        countdown = countdown - 1;
    } while (countdown > 0 && coord.border);
    return flags;
}

static bpc_stage1_t bpc_stage1_join(
    bpc_coord_t coord,
    bpc_window_t window,
    bpc_config_t config,
    bpc_flags_t border_flags
) {
    bpc_stage1_t joined;
    joined.coord = coord;
    joined.window = window;
    joined.config = config;
    joined.flags = config.flags | border_flags;
    return joined;
}

static int bpc_stage2_horizontal_feature(bpc_stage1_t stage1) {
    int left = stage1.window.matrix[1][0];
    int right = stage1.window.matrix[1][2];
    return bpc_abs(right - left);
}

static int bpc_stage2_vertical_feature(bpc_stage1_t stage1) {
    int up = stage1.window.matrix[0][1];
    int down = stage1.window.matrix[2][1];
    return bpc_abs(down - up);
}

static int bpc_stage2_diagonal_feature(bpc_stage1_t stage1) {
    int diag_a = stage1.window.matrix[0][0] ^ stage1.window.matrix[2][2];
    int diag_b = stage1.window.matrix[0][2] ^ stage1.window.matrix[2][0];
    return bpc_abs((diag_a & BPC_MAX_VALUE) - (diag_b & BPC_MAX_VALUE));
}

static int bpc_stage2_activity_feature(bpc_stage1_t stage1) {
    int activity = 0;
    bpc_flags_t local_flags = stage1.flags;
    local_flags &= (BPC_MASK_VALID | BPC_MASK_BORDER | BPC_MASK_TEXTURE);
    local_flags |= BPC_MASK_TEXTURE;
    local_flags = (~local_flags) ^ BPC_MASK_OUTPUT;

    for (int tap = 0; tap < BPC_KERNEL_TAPS; tap = tap + 1) {
        activity += bpc_abs(stage1.window.center - stage1.window.taps[tap]);
    }
    return (activity >> 1) + (int)(local_flags & BPC_MASK_OUTPUT);
}

static bpc_stage2_t bpc_stage2_join(
    int horizontal,
    int vertical,
    int diagonal,
    int activity,
    bpc_stage1_t stage1
) {
    bpc_stage2_t joined;
    int mask = (1 << 5) - 1;
    joined.horizontal = horizontal;
    joined.vertical = vertical;
    joined.diagonal = diagonal;
    joined.activity = activity;
    joined.flags = stage1.flags | ((activity & mask) << 4);
    if (activity > stage1.config.high_threshold) {
        joined.flags |= BPC_MASK_TEXTURE;
    }
    return joined;
}

static int bpc_stage3_horizontal_predict(bpc_stage1_t stage1, bpc_stage2_t stage2) {
    int left = stage1.window.matrix[1][0];
    int right = stage1.window.matrix[1][2];
    int bias = stage2.horizontal > stage2.vertical ? 1 : 0;
    return (left + right + bias) >> 1;
}

static int bpc_stage3_vertical_predict(bpc_stage1_t stage1, bpc_stage2_t stage2) {
    int up = stage1.window.matrix[0][1];
    int down = stage1.window.matrix[2][1];
    int bias = stage2.vertical > stage2.horizontal ? 1 : 0;
    return (up + down + bias) >> 1;
}

static int bpc_stage3_diagonal_predict(bpc_stage1_t stage1, bpc_stage2_t stage2) {
    int diag_a = (stage1.window.matrix[0][0] + stage1.window.matrix[2][2]) >> 1;
    int diag_b = (stage1.window.matrix[0][2] + stage1.window.matrix[2][0]) >> 1;
    return stage2.diagonal < stage2.activity ? diag_a : diag_b;
}

static int bpc_stage3_median_predict(const bpc_window_t *window) {
    int ordered[5] = {
        window->matrix[1][0],
        window->matrix[0][1],
        window->matrix[1][1],
        window->matrix[2][1],
        window->matrix[1][2]
    };

    for (int pass = 0; pass < 5; pass = pass + 1) {
        for (int idx = 0; idx < 4; idx = idx + 1) {
            if (ordered[idx] > ordered[idx + 1]) {
                int tmp = ordered[idx];
                ordered[idx] = ordered[idx + 1];
                ordered[idx + 1] = tmp;
            }
        }
    }
    return ordered[2];
}

static bpc_stage3_t bpc_stage3_join(
    int horizontal,
    int vertical,
    int diagonal,
    int median,
    bpc_stage2_t stage2
) {
    bpc_stage3_t joined;
    joined.horizontal = horizontal;
    joined.vertical = vertical;
    joined.diagonal = diagonal;
    joined.median = median;
    if (stage2.horizontal <= stage2.vertical && stage2.horizontal <= stage2.diagonal) {
        joined.selected = horizontal;
    } else if (stage2.vertical <= stage2.diagonal) {
        joined.selected = vertical;
    } else {
        joined.selected = diagonal;
    }
    if (stage2.activity > BPC_MAX_THRESHOLD) {
        joined.selected = median;
    }
    return joined;
}

static int bpc_stage4_bright_score(bpc_stage1_t stage1, bpc_stage3_t stage3) {
    int delta = stage1.window.center - stage3.selected;
    if (delta > stage1.config.threshold) {
        return delta;
    } else if (delta > 0) {
        return delta >> 1;
    }
    return 0;
}

static int bpc_stage4_dark_score(bpc_stage1_t stage1, bpc_stage3_t stage3) {
    int delta = stage3.selected - stage1.window.center;
    if (delta > stage1.config.threshold) {
        return delta;
    } else if (delta > 0) {
        return delta >> 1;
    }
    return 0;
}

static int bpc_stage4_texture_score(bpc_stage2_t stage2, bpc_stage1_t stage1) {
    int score = 0;
    for (int tap = 0; tap < BPC_KERNEL_TAPS; tap = tap + 1) {
        if (tap == 4) {
            continue;
        }
        score += bpc_abs(stage1.window.center - stage1.window.taps[tap]);
    }
    return score + stage2.activity;
}

static int bpc_stage4_edge_score(bpc_stage2_t stage2) {
    int dominant = stage2.horizontal > stage2.vertical ? stage2.horizontal : stage2.vertical;
    switch (dominant > stage2.diagonal ? 0 : 1) {
        case 0:
            return dominant;
        case 1:
            return stage2.diagonal;
        default:
            break;
    }
    return 0;
}

static bpc_stage4_t bpc_stage4_join(
    int bright,
    int dark,
    int texture,
    int edge,
    bpc_stage1_t stage1
) {
    bpc_stage4_t joined;
    joined.bright = bright;
    joined.dark = dark;
    joined.texture = texture;
    joined.edge = edge;
    joined.state = BPC_PIXEL_CLEAN;
    if (stage1.coord.border) {
        joined.state = BPC_PIXEL_BORDER;
    } else if (texture > stage1.config.high_threshold) {
        joined.state = BPC_PIXEL_TEXTURE;
    } else if (bright > dark && bright > stage1.config.threshold) {
        joined.state = BPC_PIXEL_BRIGHT;
    } else if (dark > stage1.config.threshold) {
        joined.state = BPC_PIXEL_DARK;
    }
    return joined;
}

static int bpc_stage5_replacement_lane(bpc_stage1_t stage1, bpc_stage3_t stage3, bpc_stage4_t stage4) {
    if (stage4.state == BPC_PIXEL_BRIGHT || stage4.state == BPC_PIXEL_DARK) {
        return stage3.selected;
    }
    if (stage4.state == BPC_PIXEL_TEXTURE) {
        return stage3.median;
    }
    return stage1.window.center;
}

static int bpc_stage5_confidence_lane(bpc_stage1_t stage1, bpc_stage4_t stage4) {
    int defect_score = stage4.bright > stage4.dark ? stage4.bright : stage4.dark;
    int confidence = defect_score > stage1.config.high_threshold ? 255 : (defect_score > stage1.config.threshold ? 128 : 32);
    return confidence;
}

static bpc_flags_t bpc_stage5_flag_pack_lane(bpc_stage1_t stage1, bpc_stage4_t stage4, bpc_stage2_t stage2) {
    bpc_flags_t flags = stage1.flags | stage2.flags;
    flags &= (BPC_MASK_VALID | BPC_MASK_BORDER | BPC_MASK_TEXTURE | BPC_MASK_DEFECT | BPC_MASK_OUTPUT | (31 << 4));
    if (stage4.state == BPC_PIXEL_BRIGHT || stage4.state == BPC_PIXEL_DARK) {
        flags |= BPC_MASK_DEFECT;
    }
    flags |= ((bpc_flags_t)stage4.state << 8);
    return flags;
}

static int bpc_stage5_range_lane(int replacement, int confidence) {
    int weighted = replacement + (confidence >> 5);
    return weighted < 0 ? 0 : (weighted > BPC_MAX_VALUE ? BPC_MAX_VALUE : weighted);
}

static bpc_stage5_t bpc_stage5_join(
    int replacement,
    int confidence,
    bpc_flags_t flags,
    int clamped
) {
    bpc_stage5_t joined;
    joined.replacement = replacement;
    joined.confidence = confidence;
    joined.packed_flags = flags | BPC_MASK_OUTPUT;
    joined.clamped = clamped;
    return joined;
}

static int bpc_stage6_output_value(bpc_stage1_t stage1, bpc_stage5_t stage5) {
    if ((stage5.packed_flags & BPC_MASK_DEFECT) != 0) {
        return stage5.clamped;
    }
    return stage1.window.center;
}

static bpc_flags_t bpc_stage6_status_flag(bpc_stage5_t stage5, bpc_stage4_t stage4) {
    bpc_flags_t status = stage5.packed_flags;
    status |= ((bpc_flags_t)stage4.state << 12);
    return status;
}

static void bpc_stage6_stat_accum(int *stats, int value, bpc_flags_t status) {
    int local_count = 0;
    int retry = BPC_KERNEL_SIZE;
    if (stats == NULL) {
        return;
    }
    stats[0] += value;
    stats[1] += (int)(status & BPC_MASK_DEFECT);
    local_count++;
    --retry;
    stats[2] += local_count + retry;
}

static int bpc_stage6_debug_format(struct bpc_debug_record *debug, int value, bpc_flags_t status) {
    FILE *stream;
    int length;
    int capacity;
    if (debug == NULL) {
        return 0;
    }
    capacity = (int)sizeof(debug->text);
    debug->samples[0] = value;
    debug->samples[1] = (int)status;
    debug->flags[0] = status;
    debug->flags[1] = status & BPC_MASK_DEFECT;
    length = sprintf(debug->text, "value=%d status=%u", value, status);
    if (length >= capacity) {
        debug->text[capacity - 1] = 0;
    }
    if ((status & BPC_MASK_DEFECT) != 0) {
        printf("%s\n", debug->text);
    }
    stream = fopen("/tmp/cvas_bpc_debug.log", "a");
    if (stream != NULL) {
        fprintf(stream, "%s\n", debug->text);
        fclose(stream);
    }
    return length;
}

static bpc_stage6_t bpc_stage6_join(
    int value,
    bpc_flags_t status,
    int debug_len,
    struct bpc_debug_record debug
) {
    bpc_stage6_t joined;
    joined.value = value;
    joined.status = status;
    joined.debug_len = debug_len;
    joined.debug = debug;
    return joined;
}

static int simple_bpc_pixel(
    const int *raw,
    int x,
    int y,
    int width,
    int height,
    int threshold,
    int *stats
) {
    int scratch_window[BPC_KERNEL_SIZE][BPC_KERNEL_SIZE];
    struct bpc_debug_record debug;

    // Stage 1: normalize coordinates, gather the window, and prepare config.
    bpc_coord_t coord = bpc_stage1_coord_lane(x, y, width, height);
    bpc_window_t window = bpc_stage1_window_lane(raw, coord, scratch_window);
    bpc_config_t config = bpc_stage1_threshold_lane(threshold);
    bpc_flags_t border_flags = bpc_stage1_border_lane(coord);
    bpc_stage1_t stage1 = bpc_stage1_join(coord, window, config, border_flags);

    // Stage 2: independent feature extraction lanes.
    int horizontal_feature = bpc_stage2_horizontal_feature(stage1);
    int vertical_feature = bpc_stage2_vertical_feature(stage1);
    int diagonal_feature = bpc_stage2_diagonal_feature(stage1);
    int activity_feature = bpc_stage2_activity_feature(stage1);
    bpc_stage2_t stage2 = bpc_stage2_join(
        horizontal_feature,
        vertical_feature,
        diagonal_feature,
        activity_feature,
        stage1
    );

    // Stage 3: independent prediction lanes.
    int horizontal_pred = bpc_stage3_horizontal_predict(stage1, stage2);
    int vertical_pred = bpc_stage3_vertical_predict(stage1, stage2);
    int diagonal_pred = bpc_stage3_diagonal_predict(stage1, stage2);
    int median_pred = bpc_stage3_median_predict(&stage1.window);
    bpc_stage3_t stage3 = bpc_stage3_join(horizontal_pred, vertical_pred, diagonal_pred, median_pred, stage2);

    // Stage 4: defect classification lanes.
    int bright_score = bpc_stage4_bright_score(stage1, stage3);
    int dark_score = bpc_stage4_dark_score(stage1, stage3);
    int texture_score = bpc_stage4_texture_score(stage2, stage1);
    int edge_score = bpc_stage4_edge_score(stage2);
    bpc_stage4_t stage4 = bpc_stage4_join(bright_score, dark_score, texture_score, edge_score, stage1);

    // Stage 5: correction fusion lanes.
    int replacement = bpc_stage5_replacement_lane(stage1, stage3, stage4);
    int confidence = bpc_stage5_confidence_lane(stage1, stage4);
    bpc_flags_t packed_flags = bpc_stage5_flag_pack_lane(stage1, stage4, stage2);
    int clamped = bpc_stage5_range_lane(replacement, confidence);
    bpc_stage5_t stage5 = bpc_stage5_join(replacement, confidence, packed_flags, clamped);

    // Stage 6: output/status/debug lanes.
    int output_value = bpc_stage6_output_value(stage1, stage5);
    bpc_flags_t status = bpc_stage6_status_flag(stage5, stage4);
    bpc_stage6_stat_accum(stats, output_value, status);
    int debug_len = bpc_stage6_debug_format(&debug, output_value, status);
    bpc_stage6_t stage6 = bpc_stage6_join(output_value, status, debug_len, debug);

    return bpc_clamp(stage6.value, 0, BPC_MAX_VALUE);
}

void simple_bpc_frame(
    const int *raw,
    int *out,
    int width,
    int height,
    int threshold
) {
    int safe_threshold = bpc_clamp(threshold, BPC_MIN_THRESHOLD, BPC_MAX_THRESHOLD);
    int stats[3] = {0, 0, 0};
    int y = 0;

    while (y < height) {
        int x = 0;
        while (x < width) {
            int index = y * width + x;
            int corrected = simple_bpc_pixel(raw, x, y, width, height, safe_threshold, stats);
            out[index] = corrected;
            x = x + 1;
        }
        y = y + 1;
    }
}

CVAS_END
