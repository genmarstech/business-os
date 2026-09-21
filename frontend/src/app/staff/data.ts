import "server-only";

import { getOrNull } from "@/lib/api";

/**
 * The three tables a staff screen reads, and how they join.
 *
 * ── THE JOIN IS DONE HERE, NOT BY THE SERVER ───────────────────────────────
 *
 * There is no /org/staff/?expand=credential, deliberately: a personnel list
 * that carries credentials would mean a password hash in the queryset of every
 * screen that lists employees — the thing the two tables were separated to
 * avoid. So three scoped lists are fetched and joined by id here, where the
 * worst case is a screen that renders wrongly rather than a hash in a
 * response.
 *
 * Each list is already confined to the caller's tenant by the server; nothing
 * below widens that, and nothing below may start filtering by an organisation
 * id taken from a page parameter.
 */

export type {
  Assignment,
  Branch,
  Credential,
  Person,
} from "./shape";

import type { Assignment, Branch, Credential, Person } from "./shape";

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export async function staffBoard() {
  const [people, assignments, credentials, branches] = await Promise.all([
    getOrNull<Page<Person>>("/org/staff/"),
    getOrNull<Page<Assignment>>("/brn/staff-assignments/"),
    getOrNull<Page<Credential>>("/auth/staff/credentials/"),
    getOrNull<Page<Branch>>("/brn/branch/"),
  ]);

  return {
    people: rows(people),
    assignments: rows(assignments),
    credentials: rows(credentials),
    branches: rows(branches),
  };
}
