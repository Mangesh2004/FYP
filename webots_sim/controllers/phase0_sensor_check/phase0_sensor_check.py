"""Phase 0: read one real Camera frame and one Lidar scan, then quit."""
import traceback
from pathlib import Path

LOG = Path(r"D:\FYP\webots_sim\phase0_log.txt")


def _log(text: str) -> None:
    LOG.write_text(text, encoding="utf-8")
    print(text, flush=True)


try:
    from controller import Supervisor
except Exception:
    _log(traceback.format_exc())
    raise


def main() -> None:
    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())
    camera = robot.getDevice("camera")
    lidar = robot.getDevice("lidar")
    camera.enable(timestep)
    lidar.enable(timestep)
    if robot.step(timestep) == -1:
        raise SystemExit("simulation ended before the first sensor sample")

    width = camera.getWidth()
    height = camera.getHeight()
    image = camera.getImage()
    expected = width * height * 4
    if image is None or len(image) != expected:
        raise SystemExit(f"camera image missing or wrong size: {None if image is None else len(image)} != {expected}")

    center = (height // 2) * width + (width // 2)
    base = center * 4
    blue, green, red = image[base], image[base + 1], image[base + 2]
    total = 0
    for index in range(0, len(image), 4):
        total += image[index] + image[index + 1] + image[index + 2]
    mean = total / (width * height * 3)

    ranges = lidar.getRangeImage()
    finite = [value for value in ranges if value == value and value < lidar.getMaxRange()]
    nearest = min(finite) if finite else float("inf")

    image_path = Path(r"D:\FYP\webots_sim\phase0_camera.jpg")
    camera.saveImage(str(image_path), 95)
    _log(
        f"PHASE0_OK camera={width}x{height} bytes={len(image)} "
        f"center_rgb=({red},{green},{blue}) mean={mean:.2f} "
        f"lidar_rays={len(ranges)} nearest_m={nearest:.3f} "
        f"image={image_path}"
    )
    while robot.step(timestep) != -1:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log(traceback.format_exc())
        raise
