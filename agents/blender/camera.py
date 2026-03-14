"""Camera matching — converts Vision-estimated camera params to Blender camera.

The camera parameters come from Claude Vision's space analysis (STEP 6).
Perfect accuracy isn't required since AI compositor corrects minor mismatches.
"""

import math

import bpy

# Default camera parameters (typical Korean apartment kitchen photo)
DEFAULT_CAMERA = {
    "camera_height_mm": 1300,
    "camera_distance_mm": 3000,
    "camera_tilt_deg": -2,
    "focal_length_mm": 28,
}


def setup_camera(camera_params, wall_width=3000, wall_height=2400):
    """Set up Blender camera to match the original photo's perspective.

    Args:
        camera_params: Dict with camera_height_mm, camera_distance_mm,
                       camera_tilt_deg, focal_length_mm
        wall_width: Total wall width (mm) for centering
        wall_height: Wall height (mm) for reference

    Returns:
        Blender camera object
    """
    params = {**DEFAULT_CAMERA}
    if camera_params:
        params.update({k: v for k, v in camera_params.items() if v is not None})

    # Camera position: centered on wall, at specified distance and height
    cam_x = wall_width / 2
    cam_y = params["camera_distance_mm"]  # positive = in front of wall
    cam_z = params["camera_height_mm"]

    # Create camera
    cam_data = bpy.data.cameras.new("SceneCamera")
    cam_data.lens = params["focal_length_mm"]
    cam_data.sensor_width = 36  # standard 35mm sensor

    cam_obj = bpy.data.objects.new("SceneCamera", cam_data)
    bpy.context.scene.collection.objects.link(cam_obj)

    # Position camera (Blender: Y is depth, negative is towards camera)
    cam_obj.location = (cam_x, cam_y, cam_z)

    # Point camera at wall center
    target_x = wall_width / 2
    target_y = 0  # wall is at Y=0
    target_z = wall_height / 2

    # Calculate rotation to look at target
    dx = target_x - cam_x
    dy = target_y - cam_y
    dz = target_z - cam_z

    # Rotation: camera looks down -Z in its local space
    # We need to rotate it to face the wall
    rot_x = math.atan2(math.sqrt(dx**2 + dy**2), -dz) - math.pi
    rot_z = math.atan2(dx, -dy)

    # Apply tilt adjustment
    tilt_rad = math.radians(params["camera_tilt_deg"])
    rot_x += tilt_rad

    cam_obj.rotation_euler = (rot_x, 0, rot_z)

    # Set as active camera
    bpy.context.scene.camera = cam_obj

    return cam_obj
