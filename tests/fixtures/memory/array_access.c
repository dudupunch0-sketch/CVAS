// Test: Array indexing
// Expected: load operations for array reads
CVAS_START
int sum_array(int *arr, int len) {
    int sum = 0;
    for (int i = 0; i < len; i++) {
        sum += arr[i];  // Load operation
    }
    return sum;
}
CVAS_END
