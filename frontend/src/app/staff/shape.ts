/**
 * The shapes the staff screens pass around, and the role catalogue.
 *
 * ── SEPARATE FROM data.ts BECAUSE A CLIENT COMPONENT READS IT ──────────────
 * forms.tsx needs ROLES to render a select. data.ts is `server-only` — it
 * forwards the caller's cookies — so importing it from a client component is
 * a build error, and rightly: that is how a session cookie ends up in a
 * browser bundle. This file holds the half that is safe to ship.
 */

export type Person = {
  id: number;
  full_name: string;
  email: string;
  phone_number: string;
  city?: string;
  kra_pin?: string;
  id_number?: number;
  staff_number?: string;
};

export type Assignment = {
  id: number;
  staff_member: number;
  branch: { id: number; branch_name: string } | null;
  staff_assignment: string;
  staff_assignment_display: string;
  is_active: boolean;
};

export type Credential = {
  id: number;
  staff: number;
  username: string;
  must_change_password: boolean;
  /**
   * Nobody has ever chosen a password for this login.
   *
   * The question `must_change_password` cannot answer: that flag is true both
   * for a login whose password the manager typed and for one that has no
   * password at all. They need different sentences and different buttons —
   * "they are still on the password you typed" against "they have not set
   * theirs up yet" — so the server reports them apart.
   */
  needs_setup: boolean;
  is_active: boolean;
  is_locked: boolean;
};

export type Branch = { id: number; branch_name: string };

/** The roles a branch assignment may carry, mirroring branches/models.py. */
export const ROLES: { value: string; label: string; note: string }[] = [
  { value: "CA", label: "Cashier", note: "Takes sales at a till." },
  {
    value: "SA",
    label: "Sales associate",
    note: "The same as a cashier on this platform.",
  },
  {
    value: "AM",
    label: "Assistant manager",
    note: "Runs the branch: voids, refunds, stock and its figures.",
  },
  {
    value: "IC",
    label: "Inventory clerk",
    note: "Counts and moves stock. Cannot sell.",
  },
  {
    value: "PO",
    label: "Purchasing officer",
    note: "Stock, with buying in mind. Purchasing itself comes later.",
  },
  {
    value: "FC",
    label: "Finance clerk",
    note: "Reads the money. Changes none of it.",
  },
  {
    value: "BA",
    label: "Branch auditor",
    note: "Reads everything at the branch, changes nothing.",
  },
];
