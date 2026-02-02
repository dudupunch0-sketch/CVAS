// ISP Algorithm Example
// Demonstrates various features of CVAS parser

#include <stdint.h>

// This is outside CVAS region - will be ignored
void init_hardware(void) {
    // Hardware initialization
}

CVAS_START

// Example 1: Simple arithmetic operations
int add_values(int a, int b) {
    int sum = a + b;
    return sum;
}

// Example 2: Expression with operator precedence
int calculate(int x, int y, int z) {
    // Parser should handle: multiply first, then add
    int result = x + y * z;
    return result;
}

// Example 3: Conditional logic
int clamp(int value, int min, int max) {
    int result = value;

    if (value < min) {
        result = min;
    }

    if (value > max) {
        result = max;
    }

    return result;
}

// Example 4: Complex expression
int edge_detect(int center, int left, int right, int top, int bottom) {
    // Horizontal gradient
    int h_diff = (center - left) + (center - right);

    // Vertical gradient
    int v_diff = (center - top) + (center - bottom);

    // Total edge strength
    int edge = h_diff + v_diff;

    // Clamp to maximum
    if (edge > 100) {
        edge = 100;
    }

    return edge;
}

// Example 5: Loop structure
int accumulate(int *data, int size) {
    int sum = 0;
    int i = 0;

    while (i < size) {
        sum = sum + data[i];
        i = i + 1;
    }

    return sum;
}

// Example 6: Function calls
int process_pixel(int pixel, int threshold) {
    // Call other functions
    int clamped = clamp(pixel, 0, 255);
    int doubled = add_values(clamped, clamped);

    // Use result
    if (doubled > threshold) {
        return threshold;
    }

    return doubled;
}

// Example 7: Multiple operations
int color_correction(int r, int g, int b, int gain) {
    // Apply gain to each channel
    int r_adj = r * gain;
    int g_adj = g * gain;
    int b_adj = b * gain;

    // Calculate luminance (simplified)
    int luma = r_adj + g_adj + b_adj;
    int avg = luma / 3;

    return avg;
}

CVAS_END

// Outside CVAS region - will be ignored
int main(void) {
    int result = add_values(10, 20);
    return 0;
}
