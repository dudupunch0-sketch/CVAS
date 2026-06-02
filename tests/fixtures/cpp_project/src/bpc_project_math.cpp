#include "bpc_project_types.hpp"

int project_sum_row_array(int (*row)[4], int count) {
    int total = 0;
    for (int i = 0; i < count; ++i) {
        total += (*row)[i];
    }
    return total;
}

int project_sum_grid_array(int (*grid)[3][4]) {
    int total = 0;
    for (int y = 0; y < 3; ++y) {
        for (int x = 0; x < 4; ++x) {
            total += (*grid)[y][x];
        }
    }
    return total;
}

int project_load_pixel(const int *raw, int x, int y, int width, int height) {
    int clamped_x = project_clamp_value<int>(x, 0, width - 1);
    int clamped_y = project_clamp_value<int>(y, 0, height - 1);
    return raw[clamped_y * width + clamped_x];
}

BpcProjectWindow project_load_window(const int *raw, const BpcProjectCoord &coord) {
    BpcProjectWindow window = {0, {0, 0, 0, 0}};
    const int offsets[4] = {-1, 0, 1, 2};
    window.center = project_load_pixel(raw, coord.x, coord.y, coord.width, coord.height);
    for (int tap = 0; tap < 4; ++tap) {
        window.taps[tap] = project_load_pixel(
            raw,
            coord.x + offsets[tap],
            coord.y,
            coord.width,
            coord.height
        );
    }
    return window;
}

int *project_allocate_line(int width) {
    int *line = new int[width];
    for (int i = 0; i < width; ++i) {
        line[i] = i;
    }
    return line;
}

void project_release_line(int *line) {
    delete[] line;
}

int ***project_allocate_cube(int depth, int height, int width) {
    int ***cube = new int**[depth];
    for (int z = 0; z < depth; ++z) {
        cube[z] = new int*[height];
        for (int y = 0; y < height; ++y) {
            cube[z][y] = new int[width];
            for (int x = 0; x < width; ++x) {
                cube[z][y][x] = z + y + x;
            }
        }
    }
    return cube;
}

void project_release_cube(int ***cube, int depth, int height) {
    for (int z = 0; z < depth; ++z) {
        for (int y = 0; y < height; ++y) {
            delete[] cube[z][y];
        }
        delete[] cube[z];
    }
    delete[] cube;
}
