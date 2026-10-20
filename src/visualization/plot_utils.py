import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
from typing import Optional, Sequence, Tuple

PALETTE = ['#2f6feb', '#e8833a', '#3fa66a', '#b4545b', '#7a6bd6', '#0f8b8d', '#8a8f98']
TEMP_REFERENCE_C = 35.0
PUE_REFERENCE = 1.6


def _render(fig: plt.Figure, save_path: Optional[str]) -> plt.Figure:
    """Save and close. Closing matters inside a long-running web process."""
    if save_path:
        fig.savefig(save_path, dpi=300, bbox_inches='tight')
    plt.close(fig)
    return fig


def _apply_style(style: str) -> None:
    try:
        plt.style.use(style)
    except Exception:
        plt.style.use('default')


def plot_energy_comparison(
    df: pd.DataFrame,
    save_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
    style: str = 'seaborn-v0_8-darkgrid'
) -> plt.Figure:
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)
    sns.lineplot(data=df, x='time', y='total_energy', hue='algorithm', marker='o', ax=ax)
    ax.set_title('Energy Consumption Comparison')
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Total Energy (W)')
    ax.legend(title='Algorithm')
    ax.grid(True)
    return _render(fig, save_path)


def plot_temperature_trend(
    df: pd.DataFrame,
    save_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
    style: str = 'seaborn-v0_8-darkgrid',
    reference_c: Optional[float] = TEMP_REFERENCE_C,
    peak: bool = False
) -> plt.Figure:
    _apply_style(style)
    y = 'peak_temperature' if peak and 'peak_temperature' in df.columns else 'avg_temperature'
    fig, ax = plt.subplots(figsize=figsize)
    sns.lineplot(data=df, x='time', y=y, hue='algorithm', marker='s', ax=ax)
    if 'ambient_temperature' in df.columns:
        ambient = df.groupby('time')['ambient_temperature'].first().reset_index()
        ax.plot(ambient['time'], ambient['ambient_temperature'],
                color='#6b7280', linestyle=':', linewidth=1.4, label='环境温度')
    if reference_c is not None:
        ax.axhline(y=reference_c, color='red', linestyle='--',
                   label=f'参考线 ({reference_c:g}°C)')
    ax.set_title('Temperature Trend Comparison')
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Temperature (°C)')
    ax.legend()
    ax.grid(True)
    return _render(fig, save_path)


def plot_load_distribution(
    df: pd.DataFrame,
    save_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
    style: str = 'seaborn-v0_8-darkgrid'
) -> plt.Figure:
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)
    sns.lineplot(data=df, x='time', y='total_load', hue='algorithm', marker='^', ax=ax)
    ax.set_title('Total Load Distribution')
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Total Load')
    ax.legend(title='Algorithm')
    ax.grid(True)
    return _render(fig, save_path)


def plot_pue_comparison(
    df: pd.DataFrame,
    save_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
    style: str = 'seaborn-v0_8-darkgrid',
    reference: Optional[float] = PUE_REFERENCE
) -> plt.Figure:
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)
    sns.lineplot(data=df, x='time', y='pue', hue='algorithm', marker='d', ax=ax)
    if reference is not None:
        ax.axhline(y=reference, color='green', linestyle='--',
                   label=f'参考线 (PUE {reference:g})')
    ax.set_title('PUE Comparison')
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('PUE')
    ax.legend()
    ax.grid(True)
    return _render(fig, save_path)


def plot_cooling_power(
    df: pd.DataFrame,
    save_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
    style: str = 'seaborn-v0_8-darkgrid'
) -> plt.Figure:
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)
    sns.lineplot(data=df, x='time', y='cooling_power', hue='algorithm', marker='*', ax=ax)
    ax.set_title('Cooling Power Consumption')
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Cooling Power (W)')
    ax.legend(title='Algorithm')
    ax.grid(True)
    return _render(fig, save_path)


def plot_service_rate(
    df: pd.DataFrame,
    save_path: Optional[str] = None,
    figsize: Tuple[int, int] = (12, 6),
    style: str = 'seaborn-v0_8-darkgrid'
) -> plt.Figure:
    _apply_style(style)
    fig, ax = plt.subplots(figsize=figsize)
    hourly = df.copy()
    hourly['service_rate'] = hourly['assigned'] / (hourly['assigned'] + hourly['dropped']).replace(0, pd.NA)
    sns.lineplot(data=hourly, x='time', y='service_rate', hue='algorithm', marker='o', ax=ax)
    ax.set_ylim(0, 1.05)
    ax.set_title('Task Service Rate')
    ax.set_xlabel('Time (hours)')
    ax.set_ylabel('Service Rate')
    ax.legend(title='Algorithm')
    ax.grid(True)
    return _render(fig, save_path)


def plot_summary_stats(
    stats: dict,
    save_path: Optional[str] = None,
    figsize: Tuple[int, int] = (14, 10),
    style: str = 'seaborn-v0_8-darkgrid'
) -> plt.Figure:
    _apply_style(style)
    algorithms = list(stats.keys())
    metrics = ['avg_energy', 'max_temp', 'avg_pue', 'service_rate']
    metric_names = ['Avg Energy (W)', 'Peak Temp (°C)', 'Avg PUE', 'Service Rate']
    colors: Sequence[str] = [PALETTE[i % len(PALETTE)] for i in range(len(algorithms))]

    fig, axes = plt.subplots(2, 2, figsize=figsize)

    for i, (metric, name) in enumerate(zip(metrics, metric_names)):
        row, col = i // 2, i % 2
        values = [stats[algo].get(metric) or 0 for algo in algorithms]
        axes[row, col].bar(algorithms, values, color=colors)
        axes[row, col].set_title(name)
        axes[row, col].grid(True)

    plt.tight_layout()
    return _render(fig, save_path)
