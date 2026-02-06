// Test: Struct member access
// Expected: Proper handling of -> operator
CVAS_START
typedef struct { int x; int y; } Point;
int manhattan_distance(Point *p) {
    return p->x + p->y;
}
CVAS_END
