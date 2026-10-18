"""Loaded by the supervisor to emulate a power loss: wheels stop, radio and sensors stay silent."""
from controller import Robot

robot = Robot()
ts = int(robot.getBasicTimeStep())
for name in ("left wheel", "right wheel"):
    try:
        m = robot.getDevice(name)
    except Exception:
        m = None
    if m is not None:
        m.setPosition(float("inf"))
        m.setVelocity(0.0)
while robot.step(ts) != -1:
    pass
