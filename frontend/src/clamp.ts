/** True when measured content overflows its clamped box, i.e. there is hidden
 *  text worth expanding. The 1px tolerance absorbs sub-pixel rounding from
 *  `-webkit-line-clamp`, which can report `scrollHeight` a hair above
 *  `clientHeight` even when nothing is actually cut. */
export function isClamped(scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight - clientHeight > 1;
}
