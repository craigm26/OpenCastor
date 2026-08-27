# Movement Primitives — move() Parameter Patterns

## Linear (forward/back)
```python
move(linear=0.3)  # forward 0.3m
move(linear=-0.2)  # backward 0.2m
```

## Rotation (in-place)
```python
move(angular=0.5)  # turn left ~28° (0.5 rad)
move(angular=-0.5)  # turn right ~28°
move(angular=3.14)  # 180° turn (use with care — check clearance first)
```

## Combined arc
```python
move(linear=0.3, angular=0.3)  # arc forward-left
```

## Speed override
```python
move(linear=0.3, speed=0.15)  # slow (near obstacles, humans)
move(linear=0.3, speed=0.3)  # normal max
```

## Safe navigation pattern
```python
# Always: check → move small step → check → move → check
get_distance()  # pre-check
move(linear=0.3)  # step
get_distance()  # post-check
```
