// Test: Simple function call
// Expected: Call graph with caller -> callee
CVAS_START
int helper(int x) {
    return x + 1;
}
int main_func(int input) {
    return helper(input);
}
CVAS_END
