#include <string>

CVAS_START

template<class T>
inline T clamp_value(T value, T low, T high) {
    return value < low ? low : (value > high ? high : value);
}

class BaseProcessor {
public:
    static const int kMaxSamples = 16;
    static const bool kEnabled = true;

    BaseProcessor();
    virtual ~BaseProcessor();
    virtual int process(int value, const int &readonly_ref) = 0;
    virtual const char *label() const {
        return "base";
    }

    static int scale_value(int value) {
        static int call_count = 0;
        call_count += 1;
        return value + call_count;
    }

private:
    struct ScratchSlot {
        int index;
        int value;
    };

    ScratchSlot slots[kMaxSamples];
};

BaseProcessor::BaseProcessor() {
}

BaseProcessor::~BaseProcessor() {
}

class DerivedProcessor : public BaseProcessor {
public:
    explicit DerivedProcessor(const std::string& name);
    virtual ~DerivedProcessor();

    virtual int process(int value, const int &readonly_ref) {
        int mutable_value = value;
        return adjust(mutable_value, readonly_ref, name_);
    }

    virtual const char *label() const {
        return name_.c_str();
    }

    static int adjust(int &mutable_ref, const int &readonly_ref, const std::string& tag);

private:
    struct ScratchSlot {
        int index;
        int value;
    };

    std::string name_;
    ScratchSlot local_slots[BaseProcessor::kMaxSamples];
};

DerivedProcessor::DerivedProcessor(const std::string& name) : name_(name) {
}

DerivedProcessor::~DerivedProcessor() {
}

int DerivedProcessor::adjust(int &mutable_ref, const int &readonly_ref, const std::string& tag) {
    static int call_count = 0;
    call_count += 1;
    mutable_ref += readonly_ref;
    if (tag.size() > 0) {
        mutable_ref += (int)tag.size();
    }
    return BaseProcessor::scale_value(mutable_ref + call_count);
}

const char *select_processor_label(const BaseProcessor *processor) {
    if (processor == 0) {
        return "none";
    }
    return processor->label();
}

int sum_row_array(int (*row)[4], int count) {
    int total = 0;
    for (int i = 0; i < count; ++i) {
        total += (*row)[i];
    }
    return total;
}

int sum_grid_array(int (*grid)[3][4]) {
    int total = 0;
    for (int y = 0; y < 3; ++y) {
        for (int x = 0; x < 4; ++x) {
            total += (*grid)[y][x];
        }
    }
    return total;
}

int *allocate_line(int width) {
    int *line = new int[width];
    for (int i = 0; i < width; ++i) {
        line[i] = i;
    }
    return line;
}

void release_line(int *line) {
    delete[] line;
}

int ***allocate_cube(int depth, int height, int width) {
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

void release_cube(int ***cube, int depth, int height) {
    for (int z = 0; z < depth; ++z) {
        for (int y = 0; y < height; ++y) {
            delete[] cube[z][y];
        }
        delete[] cube[z];
    }
    delete[] cube;
}

int run_cpp_syntax_fixture(int input) {
    std::string name = "derived";
    DerivedProcessor processor(name);
    int row[4] = {1, 2, 3, 4};
    int grid[3][4] = {
        {1, 2, 3, 4},
        {5, 6, 7, 8},
        {9, 10, 11, 12},
    };
    int readonly = sum_row_array(&row, 4) + sum_grid_array(&grid);
    int value = processor.process(input, readonly);
    int *line = allocate_line(4);
    int ***cube = allocate_cube(1, 1, 4);
    value += line[0] + cube[0][0][0];
    release_cube(cube, 1, 1);
    release_line(line);
    return clamp_value<int>(value, 0, 4095);
}

CVAS_END
