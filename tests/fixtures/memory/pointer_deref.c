// Test: Pointer dereference
// Expected: load and store operations
CVAS_START
int swap_values(int *a, int *b) {
    int temp = *a;  // Load
    *a = *b;        // Store
    *b = temp;      // Store
    return temp;
}
CVAS_END
