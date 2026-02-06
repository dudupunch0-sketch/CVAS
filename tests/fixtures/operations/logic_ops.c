// Test: Logical operators
// Expected: 2 logic operations (AND, OR)
CVAS_START
int logic_ops(int a, int b, int c) {
    int and_result = a && b;
    int or_result = a || c;
    return and_result + or_result;
}
CVAS_END
