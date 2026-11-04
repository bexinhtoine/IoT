"""Thư viện vẽ sơ đồ bằng matplotlib cho báo cáo (không cần Graphviz)."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle  # noqa: E402

plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]
plt.rcParams["font.size"] = 9

# Bảng màu (xanh dương chủ đạo, nhấn cam/đỏ/xanh lá)
BLUE = "#2E5496"
BLUE_FILL = "#EAF1FB"
GREY = "#555555"
GREY_FILL = "#F3F3F3"
GREEN = "#2E7D32"
GREEN_FILL = "#E8F5E9"
ORANGE = "#E65100"
ORANGE_FILL = "#FFF3E0"
RED = "#C62828"
RED_FILL = "#FFEBEE"
PURPLE = "#6A1B9A"
PURPLE_FILL = "#F3E5F5"
TEAL = "#00695C"
TEAL_FILL = "#E0F2F1"


def new_fig(w: float, h: float, xlim: tuple[float, float], ylim: tuple[float, float]):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, title="", body="", fc=BLUE_FILL, ec=BLUE, fs=9, bfs=None, lw=1.3, rounded=True, bold=True, align="center"):
    """Hộp bo góc với tiêu đề đậm và thân chữ nhỏ hơn. (x, y) là góc trái dưới."""
    style = "round,pad=0.02,rounding_size=0.18" if rounded else "square,pad=0.02"
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=style, fc=fc, ec=ec, lw=lw))
    bfs = bfs or fs - 1.2
    cx = x + w / 2 if align == "center" else x + 0.15
    ha = "center" if align == "center" else "left"
    if title and body:
        ax.text(cx, y + h - 0.12, title, ha=ha, va="top", fontsize=fs, fontweight="bold" if bold else "normal", color="#111")
        ax.text(cx, y + 0.1, body, ha=ha, va="bottom", fontsize=bfs, color="#222", linespacing=1.25)
    elif title:
        ax.text(cx, y + h / 2, title, ha=ha, va="center", fontsize=fs, fontweight="bold" if bold else "normal", color="#111", linespacing=1.25)
    elif body:
        ax.text(cx, y + h / 2, body, ha=ha, va="center", fontsize=bfs, color="#222", linespacing=1.25)
    return (x, y, w, h)


def center(b):
    return (b[0] + b[2] / 2, b[1] + b[3] / 2)


def left(b):
    return (b[0], b[1] + b[3] / 2)


def right(b):
    return (b[0] + b[2], b[1] + b[3] / 2)


def top(b):
    return (b[0] + b[2] / 2, b[1] + b[3])


def bottom(b):
    return (b[0] + b[2] / 2, b[1])


def arrow(ax, p1, p2, label="", color=GREY, lw=1.3, rad=0.0, dashed=False, fs=7.5, lpos=0.5, loff=(0, 0.12), style="-|>", lcolor=None, bg=True):
    """Mũi tên từ p1 tới p2 với nhãn đặt tại tỉ lệ lpos dọc đoạn thẳng (cộng loff)."""
    a = FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=11, color=color, lw=lw,
                        linestyle=(0, (4, 3)) if dashed else "-", connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2)
    ax.add_patch(a)
    if label:
        lx = p1[0] + (p2[0] - p1[0]) * lpos + loff[0]
        ly = p1[1] + (p2[1] - p1[1]) * lpos + loff[1]
        # dịch nhãn theo độ cong
        if rad:
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            lx += -dy * rad * 0.5
            ly += dx * rad * 0.5
        ax.text(lx, ly, label, ha="center", va="center", fontsize=fs, color=lcolor or color,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9) if bg else None, linespacing=1.15)


def label(ax, x, y, s, fs=8, color="#222", ha="center", va="center", bold=False, italic=False, bg=None):
    ax.text(x, y, s, ha=ha, va=va, fontsize=fs, color=color, fontweight="bold" if bold else "normal",
            style="italic" if italic else "normal", linespacing=1.25,
            bbox=dict(boxstyle="round,pad=0.2", fc=bg, ec="none") if bg else None)


def region(ax, x, y, w, h, title="", fc="#FAFAFA", ec="#BBBBBB", fs=9, dashed=True, tcolor="#444"):
    """Vùng nền (nhóm) có tiêu đề ở góc trái trên."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.25", fc=fc, ec=ec, lw=1.0,
                                linestyle=(0, (5, 3)) if dashed else "-"))
    if title:
        ax.text(x + 0.15, y + h - 0.1, title, ha="left", va="top", fontsize=fs, fontweight="bold", color=tcolor)


def lifeline(ax, x, ytop, ybot, title, fc=BLUE_FILL, ec=BLUE, w=2.2, h=0.6):
    box(ax, x - w / 2, ytop, w, h, title, fc=fc, ec=ec, fs=9)
    ax.plot([x, x], [ybot, ytop], color="#999", lw=1.0, linestyle=(0, (4, 3)), zorder=0)


def msg(ax, x1, x2, y, text, color=GREY, dashed=False, fs=7.5, above=True):
    arrow(ax, (x1, y), (x2, y), color=color, dashed=dashed)
    ax.text((x1 + x2) / 2, y + (0.13 if above else -0.16), text, ha="center", va="bottom" if above else "top", fontsize=fs, color="#222",
            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.9))


def selfmsg(ax, x, y, text, color=GREY, fs=7.5, side=1, w=0.55):
    """Hoạt động nội bộ trên một lifeline: vẽ hộp nhỏ bên cạnh."""
    ax.add_patch(Rectangle((x - 0.08, y - 0.22), 0.16, 0.44, fc="#FFF8E1", ec=ORANGE, lw=1.0))
    ax.text(x + side * 0.18, y, text, ha="left" if side > 0 else "right", va="center", fontsize=fs, color="#222",
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#DDD"), linespacing=1.2)


def save(fig, path, dpi=200):
    fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.08, facecolor="white")
    plt.close(fig)
