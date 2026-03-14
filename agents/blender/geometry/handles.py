"""Cabinet handle geometry — style-specific handles.

Each style has a characteristic handle shape and color.
Handles are attached to door/drawer fronts.
"""

import bpy


# Handle style definitions: (length_mm, style_type, color_rgb)
HANDLE_STYLES = {
    "modern": {"length": 128, "type": "bar", "color": (0.15, 0.15, 0.15)},       # dark nickel
    "nordic": {"length": 128, "type": "bar", "color": (0.7, 0.7, 0.72)},         # brushed silver
    "classic": {"length": 96, "type": "arch", "color": (0.72, 0.53, 0.04)},      # brass
    "natural": {"length": 96, "type": "bar", "color": (0.4, 0.35, 0.3)},         # dark bronze
    "industrial": {"length": 160, "type": "bar", "color": (0.1, 0.1, 0.1)},      # matte black
    "luxury": {"length": 128, "type": "arch", "color": (0.83, 0.69, 0.22)},      # gold
}


def create_handle(
    style="modern",
    position=(0, 0, 0),
    vertical=True,
):
    """Create a handle at the specified position.

    Args:
        style: Style name matching HANDLE_STYLES
        position: (x, y, z) in mm
        vertical: True for vertical orientation, False for horizontal

    Returns:
        Handle mesh object
    """
    config = HANDLE_STYLES.get(style, HANDLE_STYLES["modern"])
    length = config["length"]
    handle_type = config["type"]

    x, y, z = position

    if handle_type == "bar":
        # Bar handle: cylinder with two mounting posts
        bar_radius = 5
        post_radius = 4
        standoff = 25  # distance from door surface

        # Main bar
        bpy.ops.mesh.primitive_cylinder_add(
            radius=bar_radius,
            depth=length,
            location=(x, y - standoff, z),
        )
        bar = bpy.context.active_object
        bar.name = f"Handle_bar_{x:.0f}_{z:.0f}"

        if vertical:
            # Already vertical (default cylinder orientation along Z)
            pass
        else:
            import math
            bar.rotation_euler.y = math.radians(90)

        # Mounting posts
        for offset in [-length / 2 + 10, length / 2 - 10]:
            post_z = z + offset if vertical else z
            post_x = x if vertical else x + offset

            bpy.ops.mesh.primitive_cylinder_add(
                radius=post_radius,
                depth=standoff,
                location=(post_x, y - standoff / 2, post_z),
            )
            post = bpy.context.active_object
            post.name = f"Handle_post_{post_x:.0f}"
            import math
            post.rotation_euler.x = math.radians(90)
            post.parent = bar

        return bar

    elif handle_type == "arch":
        # Arch handle: curved bar
        import math
        bpy.ops.mesh.primitive_torus_add(
            major_radius=length / 2,
            minor_radius=4,
            major_segments=24,
            minor_segments=8,
            location=(x, y - 20, z),
        )
        arch = bpy.context.active_object
        arch.name = f"Handle_arch_{x:.0f}_{z:.0f}"

        # Only show half the torus (arch shape)
        # Use a boolean cutter to remove the back half
        bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y, z))
        cutter = bpy.context.active_object
        cutter.scale = (length, 50, length)
        bpy.ops.object.transform_apply(scale=True)

        mod = arch.modifiers.new(name="HalfCut", type="BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        bpy.context.view_layer.objects.active = arch
        bpy.ops.object.modifier_apply(modifier="HalfCut")
        bpy.data.objects.remove(cutter, do_unlink=True)

        if not vertical:
            arch.rotation_euler.z = math.radians(90)

        return arch

    # Fallback: simple cube handle
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, y - 15, z))
    handle = bpy.context.active_object
    handle.name = f"Handle_simple_{x:.0f}"
    handle.scale = (15 / 2, 8 / 2, length / 2)
    bpy.ops.object.transform_apply(scale=True)
    return handle
