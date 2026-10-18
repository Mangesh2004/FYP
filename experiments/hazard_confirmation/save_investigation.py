"""Save investigation evidence without altering any simulation inputs."""
import datetime
import json
from pathlib import Path

ROOT=Path('D:/FYP')
OUT=ROOT/'experiments/hazard_confirmation'
baselines=[]
for seed in [0,1,3]:
    folder='final_faults' if seed==3 else 'final_repeats'
    p=ROOT/'experiments/two_robot'/folder/'runs'/f's{seed:03d}_normal_cbba/metrics.json'
    baselines.append(dict(path=str(p),metrics=json.loads(p.read_text())))
report=dict(
    created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    causes=[
        'Euclidean proximity and failed navigation could start verification across a wall.',
        'Fixed approach vectors could land outside the hazard room or against obstacles.',
        'Old map coordinates could point the camera away from the detected object.',
        'A missing camera match permanently rejected strong sensor evidence.',
        'Averages admitted isolated sensor spikes; stale samples were not explicitly excluded.',
        'Distant box-height estimates could produce inaccurate confirmed locations.',
        'Visual-only person reports accepted low-detail observations with the same range ambiguity.',
        'Abandoned doorway approaches retained granted reservations and exhausted staging failures, preventing further room inspection.'],
    fixes=[
        'Require reaching a room-safe, locally clear viewpoint, with bounded retries.',
        'Aim from current camera bearings; search when the object is not reacquired.',
        'Require eight finite fresh sensor samples and three independent detailed camera observations.',
        'Use median sensor readings and preserve contradictory strong sensor evidence across views.',
        'Refine tentative positions and move closer when the image lacks detail.',
        'Confirmed positions use only detailed camera frames and their own freshness timestamp.',
        'Apply the detailed-frame rule to visual-only person reports.',
        'Log readings, thresholds, sample counts, camera quality and decisions.',
        'Cancel abandoned reservations only with 0.9m approach-side clearance, including after a later safe retreat.'],
    baseline_runs=baselines,
    reproduced_gas_miss=dict(sensor_ppm=487.466,visual_hits=0,confirmed=False),
    wall_facing_camera=str(OUT/'diagnostic/runs/s000_normal_cbba/verification_frames/B_85.088.jpg'),
    calibration_file=str(OUT/'calibration/results.json'),
    calibration=dict(actual_distance_m=5.4,box_estimated_range_m=3.6976,lidar_reading_m=5.6513),
    rejected_candidate_dirs=[str(OUT/'final'),str(OUT/'final_verified'),str(OUT/'verified')],
    rejected_full_suite=dict(tests=136,passed=133,failed_seeds=[5,11,1000],
        report=str(OUT/'full_suite_verified.xml'),reason='Two stuck reservations and one unfinished longer survey at 300s.'),
    hazard_matrix_time_budget_s=600,
    regression_test=str(ROOT/'webots_sim/tests/test_hazard_verification.py'),
    acceptance_test=str(ROOT/'webots_sim/tests/test_hazards_webots.py'),
    unchanged='Detector weights and score threshold, specialty-sensor thresholds, world and motor guard.',
    projection_reference='https://www.cyberbotics.com/doc/reference/camera?tab=c',
    limits=[
        'Box-size localization remains approximate; detailed views constrain the tested failure mode.',
        'Thermal sensing uses Webots LightSensor; gas uses a simulated plume model.',
        'Terminal inconclusive tasks are logged and closed for this mission, without automatic reopening.',
        'Randomized scene layouts share the public building floor plan.',
        'The existing observer uses a 1.6m hazard-match radius; its thresholds were not changed.'])
(OUT/'investigation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print('Saved investigation and original failure references.')
