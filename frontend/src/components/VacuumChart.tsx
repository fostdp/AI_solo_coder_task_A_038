import { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';
import type { VacuumDataPoint } from '@/types';

interface VacuumChartProps {
  data: VacuumDataPoint[];
  shelfIds: number[];
  height?: number;
}

const VacuumChart = ({ data, shelfIds, height = 300 }: VacuumChartProps) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<echarts.ECharts | null>(null);
  const [isLive, setIsLive] = useState(true);

  const colors = ['#06B6D4', '#8B5CF6', '#10B981', '#F59E0B', '#EC4899'];

  useEffect(() => {
    if (!chartRef.current) return;

    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current, 'dark');
    }

    const groupedData: Record<number, { time: string; value: number }[]> = {};
    shelfIds.forEach(id => {
      groupedData[id] = [];
    });

    data.forEach(item => {
      if (groupedData[item.shelf_id]) {
        const time = new Date(item.timestamp).toLocaleTimeString('zh-CN', {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        });
        groupedData[item.shelf_id].push({ time, value: item.value });
      }
    });

    const allTimes = new Set<string>();
    Object.values(groupedData).forEach(arr => {
      arr.forEach(item => allTimes.add(item.time));
    });
    const sortedTimes = Array.from(allTimes).sort();

    const series: echarts.LineSeriesOption[] = shelfIds.map((shelfId, index) => {
      const shelfData = groupedData[shelfId] || [];
      const values = sortedTimes.map(time => {
        const found = shelfData.find(d => d.time === time);
        return found ? found.value : null;
      });

      return {
        name: `搁板 ${shelfId}`,
        type: 'line' as const,
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        showSymbol: false,
        lineStyle: {
          width: 2,
          color: colors[index % colors.length],
        },
        itemStyle: {
          color: colors[index % colors.length],
        },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: colors[index % colors.length] + '40' },
            { offset: 1, color: colors[index % colors.length] + '05' },
          ]),
        },
        emphasis: {
          focus: 'series',
          itemStyle: {
            borderWidth: 2,
            borderColor: '#fff',
          },
        },
        data: values,
        animationDuration: 500,
      };
    });

    const option: EChartsOption = {
      backgroundColor: 'transparent',
      title: {
        text: '真空度实时曲线',
        textStyle: {
          color: '#CBD5E1',
          fontSize: 14,
          fontWeight: 'normal',
          fontFamily: 'Inter',
        },
        left: 10,
        top: 10,
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(15, 23, 42, 0.95)',
        borderColor: '#334155',
        borderWidth: 1,
        textStyle: {
          color: '#F1F5F9',
          fontFamily: 'Inter',
        },
        axisPointer: {
          type: 'cross',
          label: {
            backgroundColor: '#06B6D4',
            fontFamily: 'JetBrains Mono',
          },
        },
        formatter: (params: any) => {
          if (!Array.isArray(params)) return '';
          let result = `${params[0].axisValue}<br/>`;
          params.forEach((param: any) => {
            const value = param.value !== null ? `${Number(param.value).toFixed(4)} Pa` : '-';
            result += `${param.marker}${param.seriesName}: ${value}<br/>`;
          });
          return result;
        },
      },
      legend: {
        data: shelfIds.map(id => `搁板 ${id}`),
        top: 10,
        right: 10,
        textStyle: {
          color: '#94A3B8',
          fontSize: 12,
        },
        itemWidth: 20,
        itemHeight: 10,
      },
      grid: {
        left: '3%',
        right: '4%',
        bottom: '3%',
        top: '50px',
        containLabel: true,
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: sortedTimes,
        axisLine: {
          lineStyle: {
            color: '#334155',
          },
        },
        axisLabel: {
          color: '#64748B',
          fontSize: 10,
          fontFamily: 'JetBrains Mono',
        },
        splitLine: {
          show: true,
          lineStyle: {
            color: '#1E293B',
            type: 'dashed',
          },
        },
      },
      yAxis: {
        type: 'value',
        name: 'Pa',
        nameTextStyle: {
          color: '#64748B',
          fontSize: 11,
        },
        axisLine: {
          show: false,
        },
        axisLabel: {
          color: '#64748B',
          fontSize: 10,
          fontFamily: 'JetBrains Mono',
          formatter: (value: number) => value.toFixed(2),
        },
        splitLine: {
          lineStyle: {
            color: '#1E293B',
            type: 'dashed',
          },
        },
      },
      dataZoom: [
        {
          type: 'inside',
          start: 50,
          end: 100,
        },
        {
          type: 'slider',
          start: 50,
          end: 100,
          height: 20,
          bottom: 5,
          borderColor: 'transparent',
          backgroundColor: '#1E293B',
          fillerColor: 'rgba(6, 182, 212, 0.2)',
          handleStyle: {
            color: '#06B6D4',
          },
          textStyle: {
            color: '#64748B',
          },
        },
      ],
      series,
    };

    chartInstance.current.setOption(option, true);

    const handleResize = () => {
      chartInstance.current?.resize();
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [data, shelfIds, colors]);

  return (
    <div className="relative">
      <div
        ref={chartRef}
        style={{ width: '100%', height }}
        className="rounded-lg border border-slate-700 bg-slate-900/50"
      />
      <div className="absolute top-2 right-36 flex items-center gap-2">
        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs ${
            isLive ? 'bg-green-500/20 text-green-400' : 'bg-slate-700 text-slate-400'
          }`}
        >
          <span
            className={`w-2 h-2 rounded-full ${
              isLive ? 'bg-green-400 animate-pulse' : 'bg-slate-500'
            }`}
          />
          {isLive ? '实时' : '暂停'}
        </span>
        <button
          onClick={() => setIsLive(!isLive)}
          className="px-2 py-0.5 text-xs bg-slate-700 hover:bg-slate-600 rounded transition-colors"
        >
          {isLive ? '暂停' : '继续'}
        </button>
      </div>
    </div>
  );
};

export default VacuumChart;
