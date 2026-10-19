"""Render an STL to a PNG using FreeCAD's bundled Python.

FreeCAD ships numpy/matplotlib/PIL but the Mesh module reads STL natively, so
this script doesn't need the `numpy-stl` package. It uses matplotlib's Agg
backend (no X11 / OpenGL needed) which is the whole point — GitHub Actions
Linux runners have no display, and OpenSCAD's --imgsize PNG export
seg-faults without a display server.

Usage (under freecadcmd):
    freecadcmd python/render_stl.py -- <input.stl> <output.png> [--size WxH]
        [--color R,G,B] [--bg R,G,B] [--yaw DEG] [--overlay PATH:R,G,B]...

`--overlay` may be passed multiple times to draw additional STLs on top of
the primary input in a different colour — handy for multi-part models where
each part has its own STL but you want a single combined preview. The view
limits span the union of all meshes so nothing is clipped.

Three views are stitched left-to-right (3/4 perspective, side, top) so the
model is recognisable at a glance from a single image. Faces are flat-shaded
against a fake light direction so silhouette and orientation read clearly.

The three cameras are fixed, so a feature on the far side of a model can end
up hidden behind it in every one of them. `--yaw DEG` spins every mesh about
the Z axis (all of them together, so overlays stay registered) before the
views are drawn — turn the interesting face towards the 3/4 camera rather
than moving the feature in the CAD.
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

import Mesh  # noqa: E402  (FreeCAD)

LIGHT_DIR = np.array([0.4, -0.6, 0.7], dtype=np.float32)
LIGHT_DIR /= np.linalg.norm(LIGHT_DIR)


def _split_args(argv):
    return argv[argv.index("--") + 1:] if "--" in argv else argv[1:]


def _parse(args):
    if len(args) < 2:
        sys.exit("usage: render_stl.py -- <in.stl> <out.png> "
                 "[--size WxH] [--color R,G,B] [--bg R,G,B] [--yaw DEG] "
                 "[--overlay PATH:R,G,B]...")
    opts = {"size": (1200, 480), "color": (0.78, 0.10, 0.12),
            "bg": (0.97, 0.97, 0.97), "yaw": 0.0, "overlays": []}
    rest = list(args[2:])
    while rest:
        flag = rest.pop(0)
        val = rest.pop(0)
        if flag == "--size":
            w, h = val.lower().split("x")
            opts["size"] = (int(w), int(h))
        elif flag == "--yaw":
            opts["yaw"] = float(val)
        elif flag in ("--color", "--bg"):
            opts[flag.lstrip("-")] = tuple(float(c) for c in val.split(","))
        elif flag == "--overlay":
            path, rgb = val.rsplit(":", 1)
            opts["overlays"].append(
                (path, tuple(float(c) for c in rgb.split(",")))
            )
        else:
            sys.exit(f"unknown flag {flag}")
    return args[0], args[1], opts


def _spin(tris, degrees):
    """Rotate every vertex about the Z axis. Applied to all meshes with the
    same angle, so overlays stay registered with the base mesh."""
    if not degrees:
        return tris
    a = np.radians(degrees)
    rot = np.array([[np.cos(a), -np.sin(a), 0],
                    [np.sin(a), np.cos(a), 0],
                    [0, 0, 1]], dtype=np.float32)
    return tris @ rot.T


def _load_triangles(stl_path):
    mesh = Mesh.Mesh(stl_path)
    tris = np.empty((mesh.CountFacets, 3, 3), dtype=np.float32)
    for i, facet in enumerate(mesh.Facets):
        for j, p in enumerate(facet.Points):
            tris[i, j] = (p[0], p[1], p[2])
    return tris


def _shade(tris, base_rgb):
    # flat shading: brightness = max(0.25, dot(normal, light)) per face
    e1 = tris[:, 1] - tris[:, 0]
    e2 = tris[:, 2] - tris[:, 0]
    normals = np.cross(e1, e2)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1
    normals /= lengths
    intensity = np.clip(normals @ LIGHT_DIR, 0.25, 1.0).astype(np.float32)
    base = np.array(base_rgb, dtype=np.float32)
    return np.clip(intensity[:, None] * base + 0.05, 0, 1)


def _draw_view(ax, layers, elev, azim, title):
    # `layers` is a list of (tris, face_rgb) — drawn back-to-front in
    # given order. Used for overlaying multiple STLs in distinct
    # colours.
    all_pts = []
    for tris, face_rgb in layers:
        coll = Poly3DCollection(
            tris,
            facecolors=face_rgb,
            edgecolor=(0.1, 0.1, 0.1, 0.15),
            linewidth=0.1,
        )
        ax.add_collection3d(coll)
        all_pts.append(tris.reshape(-1, 3))

    flat = np.concatenate(all_pts, axis=0)
    mins = flat.min(axis=0)
    maxs = flat.max(axis=0)
    centre = (mins + maxs) / 2
    span = (maxs - mins).max() / 2 * 1.02
    ax.set_xlim(centre[0] - span, centre[0] + span)
    ax.set_ylim(centre[1] - span, centre[1] + span)
    ax.set_zlim(centre[2] - span, centre[2] + span)
    try:
        ax.set_box_aspect((1, 1, 1))
    except AttributeError:
        pass

    ax.view_init(elev=elev, azim=azim)
    ax.set_title(title, fontsize=10)
    ax.set_axis_off()


def main():
    in_path, out_path, opts = _parse(_split_args(sys.argv))

    base_tris = _spin(_load_triangles(in_path), opts["yaw"])
    layers = [(base_tris, _shade(base_tris, opts["color"]))]
    for path, rgb in opts["overlays"]:
        ot = _spin(_load_triangles(path), opts["yaw"])
        layers.append((ot, _shade(ot, rgb)))

    width, height = opts["size"]
    dpi = 100
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi,
                     facecolor=opts["bg"])

    views = [
        (22, -55, "3/4 view"),
        (0,  -90, "side"),
        (90, -90, "top"),
    ]
    for i, (elev, azim, title) in enumerate(views, 1):
        ax = fig.add_subplot(1, len(views), i, projection="3d")
        _draw_view(ax, layers, elev, azim, title)

    title = os.path.basename(in_path)
    if opts["overlays"]:
        title += " + " + ", ".join(
            os.path.basename(p) for p, _ in opts["overlays"]
        )
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight",
                facecolor=opts["bg"])
    total = sum(t.shape[0] for t, _ in layers)
    print(f"wrote {out_path} ({total} triangles across {len(layers)} mesh"
          f"{'es' if len(layers) > 1 else ''})")


main()
