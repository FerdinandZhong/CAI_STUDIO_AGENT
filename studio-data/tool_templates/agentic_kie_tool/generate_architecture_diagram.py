#!/usr/bin/env python3
"""
Generate architecture diagram for Agentic KIE Tool.
Creates a visual representation of the CrewAI workflow.
"""

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np


def create_architecture_diagram():
    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 12)
    ax.set_aspect('equal')
    ax.axis('off')

    # Colors
    colors = {
        'input': '#E3F2FD',       # Light blue
        'paddle': '#FFF3E0',      # Light orange
        'rolm': '#E8F5E9',        # Light green
        'master': '#F3E5F5',      # Light purple
        'output': '#FFEBEE',      # Light red
        'border': '#424242',      # Dark gray
        'arrow': '#1565C0',       # Blue
        'title': '#0D47A1',       # Dark blue
    }

    # Title
    ax.text(8, 11.5, 'Agentic KIE Tool - CrewAI Workflow Architecture',
            fontsize=18, fontweight='bold', ha='center', va='center',
            color=colors['title'])

    # Subtitle
    ax.text(8, 11, 'Multi-Agent Document Key Information Extraction',
            fontsize=12, ha='center', va='center', color='#666666')

    # ==================== INPUT SECTION ====================
    input_box = FancyBboxPatch((0.5, 8.5), 3, 2,
                                boxstyle="round,pad=0.05,rounding_size=0.2",
                                facecolor=colors['input'],
                                edgecolor=colors['border'], linewidth=2)
    ax.add_patch(input_box)
    ax.text(2, 10, 'INPUT', fontsize=11, fontweight='bold', ha='center', va='center')
    ax.text(2, 9.5, 'Image Path/URL', fontsize=9, ha='center', va='center')
    ax.text(2, 9.1, 'Target Fields', fontsize=9, ha='center', va='center')
    ax.text(2, 8.7, 'Paddle Threshold', fontsize=9, ha='center', va='center')

    # Mode selection box
    mode_box = FancyBboxPatch((0.5, 6.5), 3, 1.5,
                               boxstyle="round,pad=0.05,rounding_size=0.2",
                               facecolor='#FFF9C4',
                               edgecolor=colors['border'], linewidth=1.5)
    ax.add_patch(mode_box)
    ax.text(2, 7.5, 'Mode Selection', fontsize=10, fontweight='bold', ha='center')
    ax.text(2, 7, 'open_schema: true/false', fontsize=8, ha='center', style='italic')

    # Arrow from input to mode
    ax.annotate('', xy=(2, 8.5), xytext=(2, 8),
                arrowprops=dict(arrowstyle='->', color=colors['arrow'], lw=2))

    # ==================== AGENT 1: PADDLE ====================
    paddle_box = FancyBboxPatch((4.5, 7), 3.5, 3.5,
                                 boxstyle="round,pad=0.05,rounding_size=0.2",
                                 facecolor=colors['paddle'],
                                 edgecolor='#E65100', linewidth=2)
    ax.add_patch(paddle_box)
    ax.text(6.25, 10, 'AGENT 1', fontsize=9, ha='center', va='center', color='#E65100')
    ax.text(6.25, 9.5, 'Paddle Retrieval', fontsize=11, fontweight='bold', ha='center')
    ax.text(6.25, 9, 'Agent', fontsize=11, fontweight='bold', ha='center')

    # Paddle tool box
    paddle_tool = FancyBboxPatch((4.8, 7.2), 2.9, 1.5,
                                  boxstyle="round,pad=0.03,rounding_size=0.1",
                                  facecolor='#FFE0B2',
                                  edgecolor='#E65100', linewidth=1)
    ax.add_patch(paddle_tool)
    ax.text(6.25, 8.4, 'paddle_retrieve_tool', fontsize=8, fontweight='bold', ha='center')
    ax.text(6.25, 8, 'PaddleOCR API', fontsize=8, ha='center')
    ax.text(6.25, 7.6, 'Text Detection + Recognition', fontsize=7, ha='center', style='italic')

    # Arrow from mode to paddle
    ax.annotate('', xy=(4.5, 8.75), xytext=(3.5, 7.25),
                arrowprops=dict(arrowstyle='->', color=colors['arrow'], lw=2,
                               connectionstyle='arc3,rad=0.2'))

    # ==================== AGENT 2: ROLM ====================
    rolm_box = FancyBboxPatch((8.5, 7), 3.5, 3.5,
                               boxstyle="round,pad=0.05,rounding_size=0.2",
                               facecolor=colors['rolm'],
                               edgecolor='#2E7D32', linewidth=2)
    ax.add_patch(rolm_box)
    ax.text(10.25, 10, 'AGENT 2', fontsize=9, ha='center', va='center', color='#2E7D32')
    ax.text(10.25, 9.5, 'Rolm Discovery/', fontsize=11, fontweight='bold', ha='center')
    ax.text(10.25, 9, 'Retrieval Agent', fontsize=11, fontweight='bold', ha='center')

    # Rolm capabilities box
    rolm_caps = FancyBboxPatch((8.8, 7.2), 2.9, 1.5,
                                boxstyle="round,pad=0.03,rounding_size=0.1",
                                facecolor='#C8E6C9',
                                edgecolor='#2E7D32', linewidth=1)
    ax.add_patch(rolm_caps)
    ax.text(10.25, 8.4, 'RolmOCR VLM', fontsize=8, fontweight='bold', ha='center')
    ax.text(10.25, 8, 'Multimodal (Direct Image)', fontsize=8, ha='center')
    ax.text(10.25, 7.6, 'OpenAI-compatible API', fontsize=7, ha='center', style='italic')

    # Arrow from paddle to rolm
    ax.annotate('', xy=(8.5, 8.75), xytext=(8, 8.75),
                arrowprops=dict(arrowstyle='->', color=colors['arrow'], lw=2))
    ax.text(8.25, 9.1, 'context', fontsize=8, ha='center', color=colors['arrow'])

    # ==================== AGENT 3: MASTER ====================
    master_box = FancyBboxPatch((12.5, 7), 3, 3.5,
                                 boxstyle="round,pad=0.05,rounding_size=0.2",
                                 facecolor=colors['master'],
                                 edgecolor='#6A1B9A', linewidth=2)
    ax.add_patch(master_box)
    ax.text(14, 10, 'AGENT 3', fontsize=9, ha='center', va='center', color='#6A1B9A')
    ax.text(14, 9.5, 'Retrieval Master', fontsize=11, fontweight='bold', ha='center')
    ax.text(14, 9, 'Agent', fontsize=11, fontweight='bold', ha='center')

    # Master capabilities
    master_caps = FancyBboxPatch((12.7, 7.2), 2.6, 1.5,
                                  boxstyle="round,pad=0.03,rounding_size=0.1",
                                  facecolor='#E1BEE7',
                                  edgecolor='#6A1B9A', linewidth=1)
    ax.add_patch(master_caps)
    ax.text(14, 8.4, 'Merge & Orchestrate', fontsize=8, fontweight='bold', ha='center')
    ax.text(14, 8, 'Confidence Routing', fontsize=8, ha='center')
    ax.text(14, 7.6, 'JSON Normalization', fontsize=7, ha='center', style='italic')

    # Arrow from rolm to master
    ax.annotate('', xy=(12.5, 8.75), xytext=(12, 8.75),
                arrowprops=dict(arrowstyle='->', color=colors['arrow'], lw=2))
    ax.text(12.25, 9.1, 'context', fontsize=8, ha='center', color=colors['arrow'])

    # ==================== PROCESS FLOW ====================
    process_box = FancyBboxPatch((4.5, 5.5), 11, 1,
                                  boxstyle="round,pad=0.03,rounding_size=0.1",
                                  facecolor='#ECEFF1',
                                  edgecolor='#455A64', linewidth=1.5)
    ax.add_patch(process_box)
    ax.text(10, 6, 'CrewAI Sequential Process: paddle_task  \u2192  rolm_task  \u2192  merge_task',
            fontsize=10, ha='center', va='center', fontweight='bold', color='#37474F')

    # Arrows down to process
    for x in [6.25, 10.25, 14]:
        ax.annotate('', xy=(x, 6.5), xytext=(x, 7),
                    arrowprops=dict(arrowstyle='->', color='#455A64', lw=1.5))

    # ==================== OUTPUT SECTION ====================
    # Open schema output
    open_out = FancyBboxPatch((4, 1), 5, 3.8,
                               boxstyle="round,pad=0.05,rounding_size=0.2",
                               facecolor='#E8EAF6',
                               edgecolor='#303F9F', linewidth=2)
    ax.add_patch(open_out)
    ax.text(6.5, 4.5, 'OPEN SCHEMA OUTPUT', fontsize=10, fontweight='bold',
            ha='center', color='#303F9F')
    ax.text(6.5, 4, '(open_schema=True)', fontsize=8, ha='center', style='italic')

    # Open schema fields
    open_fields = [
        'discovered_fields: {...}',
        'canonical: {total, tax, date...}',
        'extras: {additional fields}',
        'decision_log: {paddle_summary}',
    ]
    for i, field in enumerate(open_fields):
        ax.text(6.5, 3.4 - i*0.55, field, fontsize=8, ha='center', family='monospace')

    # Closed schema output
    closed_out = FancyBboxPatch((9.5, 1), 5.5, 3.8,
                                 boxstyle="round,pad=0.05,rounding_size=0.2",
                                 facecolor='#FBE9E7',
                                 edgecolor='#BF360C', linewidth=2)
    ax.add_patch(closed_out)
    ax.text(12.25, 4.5, 'CLOSED SCHEMA OUTPUT', fontsize=10, fontweight='bold',
            ha='center', color='#BF360C')
    ax.text(12.25, 4, '(open_schema=False)', fontsize=8, ha='center', style='italic')

    # Closed schema fields
    closed_fields = [
        'paddle: {candidates}',
        'rolm: {extracted_values}',
        'final: {merged_values}',
        'routing: {from_paddle, from_rolm}',
    ]
    for i, field in enumerate(closed_fields):
        ax.text(12.25, 3.4 - i*0.55, field, fontsize=8, ha='center', family='monospace')

    # Arrow from process to outputs
    ax.annotate('', xy=(6.5, 4.8), xytext=(8, 5.5),
                arrowprops=dict(arrowstyle='->', color='#303F9F', lw=2,
                               connectionstyle='arc3,rad=-0.2'))
    ax.annotate('', xy=(12.25, 4.8), xytext=(10.5, 5.5),
                arrowprops=dict(arrowstyle='->', color='#BF360C', lw=2,
                               connectionstyle='arc3,rad=0.2'))

    # ==================== LEGEND ====================
    legend_items = [
        (colors['paddle'], 'PaddleOCR (Text Detection)'),
        (colors['rolm'], 'RolmOCR (Vision LM)'),
        (colors['master'], 'Master (Orchestration)'),
    ]

    for i, (color, label) in enumerate(legend_items):
        rect = FancyBboxPatch((0.5, 4.5 - i*0.7), 0.4, 0.4,
                               boxstyle="round,pad=0.02",
                               facecolor=color, edgecolor='#666666')
        ax.add_patch(rect)
        ax.text(1.1, 4.7 - i*0.7, label, fontsize=8, va='center')

    # Footer
    ax.text(8, 0.3, 'CrewAI Multi-Agent Workflow | Sequential Process | Deterministic Post-Processing',
            fontsize=9, ha='center', va='center', color='#757575', style='italic')

    plt.tight_layout()
    return fig


def main():
    fig = create_architecture_diagram()

    # Save as PNG
    png_path = 'agentic_kie_architecture.png'
    fig.savefig(png_path, dpi=150, bbox_inches='tight', facecolor='white')
    print(f"Saved: {png_path}")

    # Save as PDF
    pdf_path = 'agentic_kie_architecture.pdf'
    fig.savefig(pdf_path, bbox_inches='tight', facecolor='white')
    print(f"Saved: {pdf_path}")

    plt.close(fig)


if __name__ == '__main__':
    main()
