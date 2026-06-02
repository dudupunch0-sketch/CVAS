#ifndef CVAS_BPC_PROJECT_TYPES_HPP
#define CVAS_BPC_PROJECT_TYPES_HPP

#include <string>

struct BpcProjectCoord {
    int x;
    int y;
    int width;
    int height;
};

struct BpcProjectWindow {
    int center;
    int taps[4];
};

template<class T>
inline T project_clamp_value(T value, T low, T high) {
    return value < low ? low : (value > high ? high : value);
}

class BpcProjectBaseProcessor {
public:
    static const int kMaxSamples = 16;
    static const bool kEnabled = true;

    BpcProjectBaseProcessor();
    virtual ~BpcProjectBaseProcessor();
    virtual int process(int value, const int &readonly_ref) = 0;
    virtual const char *label() const {
        return "base";
    }

    static int scale_value(int value);

private:
    struct ScratchSlot {
        int index;
        int value;
    };

    ScratchSlot slots[kMaxSamples];
};

class BpcProjectDerivedProcessor : public BpcProjectBaseProcessor {
public:
    explicit BpcProjectDerivedProcessor(const std::string& name);
    virtual ~BpcProjectDerivedProcessor();

    virtual int process(int value, const int &readonly_ref);
    virtual const char *label() const;

    static int adjust(int &mutable_ref, const int &readonly_ref, const std::string& tag);

private:
    struct ScratchSlot {
        int index;
        int value;
    };

    std::string name_;
    ScratchSlot local_slots[BpcProjectBaseProcessor::kMaxSamples];
};

const char *project_select_processor_label(const BpcProjectBaseProcessor *processor);

int project_sum_row_array(int (*row)[4], int count);
int project_sum_grid_array(int (*grid)[3][4]);
int project_load_pixel(const int *raw, int x, int y, int width, int height);
BpcProjectWindow project_load_window(const int *raw, const BpcProjectCoord &coord);
int *project_allocate_line(int width);
void project_release_line(int *line);
int ***project_allocate_cube(int depth, int height, int width);
void project_release_cube(int ***cube, int depth, int height);

#endif
