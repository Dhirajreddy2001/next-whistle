export type Frame = [number, number, number, number, number, number[], 'controlled' | 'flight' | 'dead'];
export type TrackingSample = { positions: Record<string, [number, number]>; ball: [number, number] };

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));

/**
 * Where everyone is drawn at clockMs. Frames are stored twice a second, so the picture runs one frame behind the clock and
 * moves between the last two frames: players along a smooth curve using their stored velocities, the ball in a straight
 * line. Nothing is projected past the newest frame, so the ball is never drawn somewhere it has not been.
 */
export function sampleTracking(frames: Frame[], order: string[], clockMs: number): TrackingSample | null {
  if (!frames.length) return null;
  const last = frames[frames.length - 1];
  const previous = frames.length > 1 ? frames[frames.length - 2] : last;
  const spanMs = last[0] - previous[0];
  const smooth = spanMs > 0 && spanMs <= 1500; // after a seek the two frames can be far apart: show the newest one
  const u = smooth ? clamp((clockMs - last[0]) / spanMs, 0, 1) : 1;
  const seconds = spanMs / 1000;
  const h00 = 2 * u ** 3 - 3 * u ** 2 + 1, h10 = u ** 3 - 2 * u ** 2 + u, h01 = -2 * u ** 3 + 3 * u ** 2, h11 = u ** 3 - u ** 2;
  const positions = Object.fromEntries(order.map((id, index) => {
    const o = index * 4;
    const along = (axis: number) => smooth
      ? h00 * previous[5][o + axis] + h10 * seconds * previous[5][o + 2 + axis] + h01 * last[5][o + axis] + h11 * seconds * last[5][o + 2 + axis]
      : last[5][o + axis];
    return [id, [clamp(along(0), 0, 105), clamp(along(1), 0, 68)] as [number, number]];
  }));
  const ball: [number, number] = smooth
    ? [previous[1] + (last[1] - previous[1]) * u, previous[2] + (last[2] - previous[2]) * u]
    : [last[1], last[2]];
  return { positions, ball: [clamp(ball[0], 0, 105), clamp(ball[1], 0, 68)] };
}
