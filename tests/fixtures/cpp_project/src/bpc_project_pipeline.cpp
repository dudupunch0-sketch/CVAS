#include "bpc_project_types.hpp"

CVAS_START

int run_project_bpc_frame(const int *raw, int width, int height, int threshold) {
    std::string name = "project_bpc";
    BpcProjectDerivedProcessor processor(name);
    int row[4] = {1, 2, 3, 4};
    int grid[3][4] = {
        {1, 2, 3, 4},
        {5, 6, 7, 8},
        {9, 10, 11, 12},
    };
    int readonly = project_sum_row_array(&row, 4) + project_sum_grid_array(&grid);
    const char *label = project_select_processor_label(&processor);
    int value = processor.process(threshold, readonly);
    int *line = project_allocate_line(4);
    int ***cube = project_allocate_cube(1, 1, 4);
    BpcProjectCoord coord = {0, 0, width, height};
    BpcProjectWindow window = project_load_window(raw, coord);
    value += line[0] + cube[0][0][0] + window.center + label[0];
    project_release_cube(cube, 1, 1);
    project_release_line(line);
    return project_clamp_value<int>(value, 0, 4095);
}

CVAS_END
