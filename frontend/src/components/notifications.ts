"use server";

/**
 * Marking notifications read.
 *
 * ── A SERVER ACTION, LIKE EVERY OTHER WRITE IN THIS APPLICATION ───────────
 * The bell READS from the browser — a GET needs no CSRF token and the session
 * cookie goes along by itself. The write does not, and rather than teach a
 * client component to find the `csrftoken` cookie and set `x-csrftoken`, it
 * goes through `lib/api.ts` like every other write here.
 *
 * That matters more than it looks: the comment on `lib/api.ts`'s `write()`
 * records that the token comes from the sign-on callback and not from
 * /auth/me, and that getting it wrong made every subscriber write impossible
 * for a while. A second CSRF implementation in the browser is a second place
 * to rediscover that.
 */

import { post } from "@/lib/api";

export async function markRead(ids?: number[]): Promise<number> {
  try {
    const answer = await post<{ unread?: number }>(
      "/ntf/read",
      ids ? { ids } : {},
    );
    return answer?.unread ?? 0;
  } catch {
    /*
     * Swallowed, and the caller re-reads the count instead of trusting this.
     *
     * Failing to mark something read is not worth an error in somebody's face:
     * nothing is lost, the notification is still there, and the next press
     * works. The one thing that must not happen is the panel claiming a number
     * it did not get from the server.
     */
    return -1;
  }
}
