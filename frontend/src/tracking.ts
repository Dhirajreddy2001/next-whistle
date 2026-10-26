export type Frame = [number, number, number, number, number, number[], 'controlled' | 'flight' | 'dead'];
export type TrackingSample = { positions: Record<string, [number, number]>; ball: [number, number] };

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));

/** The picture is drawn this long behind the clock, so the frames on both sides of the drawn moment have already arrived. */
export const DELAY_MS = 750;

/**
 * Where everyone is at time `at`: between the two stored frames either side of it. Players move along a smooth curve using
 * their stored velocities, the ball in a straight line. Nothing is projected past the newest frame, so the ball is never
 * drawn somewhere it has not been, and it never waits for a frame that is late.
 */
export function interpolate(frames: Frame[], order: string[], at: number): TrackingSample | null {
  if (!frames.length) return null;
  let index = frames.length - 1;
  while (index > 0 && frames[index][0] > at) index--;
  const from = frames[index];
  const to = frames[index + 1];
  const spanMs = to ? to[0] - from[0] : 0;
  const smooth = spanMs > 0 && spanMs <= 1500; // after a seek the frames can be far apart: show the nearest one
  const u = smooth ? clamp((at - from[0]) / spanMs, 0, 1) : 0;
  const seconds = spanMs / 1000;
  const h00 = 2 * u ** 3 - 3 * u ** 2 + 1, h10 = u ** 3 - 2 * u ** 2 + u, h01 = -2 * u ** 3 + 3 * u ** 2, h11 = u ** 3 - u ** 2;
  const positions = Object.fromEntries(order.map((id, i) => {
    const o = i * 4;
    const along = (axis: number) => smooth && to
      ? h00 * from[5][o + axis] + h10 * seconds * from[5][o + 2 + axis] + h01 * to[5][o + axis] + h11 * seconds * to[5][o + 2 + axis]
      : from[5][o + axis];
    return [id, [clamp(along(0), 0, 105), clamp(along(1), 0, 68)] as [number, number]];
  }));
  const ball: [number, number] = smooth && to
    ? [from[1] + (to[1] - from[1]) * u, from[2] + (to[2] - from[2]) * u]
    : [from[1], from[2]];
  return { positions, ball: [clamp(ball[0], 0, 105), clamp(ball[1], 0, 68)] };
}

/** The live picture: drawn DELAY_MS behind the clock. */
export const sampleTracking = (frames: Frame[], order: string[], clockMs: number) => interpolate(frames, order, clockMs - DELAY_MS);
