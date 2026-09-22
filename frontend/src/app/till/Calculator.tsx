"use client";

import { useState } from "react";

import styles from "./till.module.css";

/**
 * A calculator, because the counter already has one and it is somebody's phone.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * WHAT IT IS FOR, AND WHAT IT IS DELIBERATELY NOT FOR.
 *
 * Not for pricing a sale — the basket does that, the server prices it, and a
 * figure typed here can never become one charged. It is for the questions a
 * counter throws up beside the till: splitting a total between two people,
 * checking a supplier's delivery note, working out what a customer is owed
 * when they want to round.
 *
 * A cashier who has to reach for their phone for that has both stopped
 * serving and taken their eyes off the drawer.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Integer cents throughout. 0.1 + 0.2 is 0.30000000000000004 in JavaScript,
 * and a calculator that cannot add two prices is worse than no calculator —
 * the same reason money.ts exists for the basket.
 */

type Op = "+" | "-" | "x" | "/";

function apply(a: number, b: number, op: Op): number {
  switch (op) {
    case "+":
      return a + b;
    case "-":
      return a - b;
    case "x":
      // Both are cents, so the product is cents² — scale back down once.
      return Math.round((a * b) / 100);
    case "/":
      return b === 0 ? NaN : Math.round((a * 100) / b);
  }
}

function show(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  return `${sign}${Math.floor(abs / 100).toLocaleString("en-KE")}.${String(
    abs % 100,
  ).padStart(2, "0")}`;
}

export function Calculator({ onClose }: { onClose: () => void }) {
  // The number being typed, as a digit string in cents. Typing shifts left,
  // which is how every till and every card machine behaves: press 1, 2, 5 and
  // you have 1.25 rather than 125.00.
  const [entry, setEntry] = useState("");
  const [left, setLeft] = useState<number | null>(null);
  const [op, setOp] = useState<Op | null>(null);

  const current = entry === "" ? (left ?? 0) : Number(entry);

  function digit(d: string) {
    if (entry.length >= 9) return;
    setEntry((entry + d).replace(/^0+(?=\d)/, ""));
  }

  function choose(next: Op) {
    const value = entry === "" ? (left ?? 0) : Number(entry);
    setLeft(left !== null && op && entry !== "" ? apply(left, value, op) : value);
    setEntry("");
    setOp(next);
  }

  function equals() {
    if (left === null || op === null) return;
    const value = entry === "" ? left : Number(entry);
    setLeft(apply(left, value, op));
    setEntry("");
    setOp(null);
  }

  function clear() {
    setEntry("");
    setLeft(null);
    setOp(null);
  }

  const keys: (string | Op)[] = [
    "7", "8", "9", "/",
    "4", "5", "6", "x",
    "1", "2", "3", "-",
    "0", "00", "=", "+",
  ];

  return (
    <div className={styles.tool} role="dialog" aria-label="Calculator">
      <div className={styles.toolHead}>
        <span className={styles.toolTitle}>Calculator</span>
        <button className={styles.toolClose} onClick={onClose}>
          Close
        </button>
      </div>

      <div className={styles.calcDisplay} aria-live="polite">
        {show(current)}
        {op ? <span className={styles.calcOp}>{op}</span> : null}
      </div>

      <div className={styles.calcKeys}>
        {keys.map((key) => {
          const isOp = ["+", "-", "x", "/"].includes(key);
          const isEquals = key === "=";
          return (
            <button
              key={key}
              type="button"
              className={`${styles.calcKey} ${isOp ? styles.calcKeyOp : ""} ${
                isEquals ? styles.calcKeyEquals : ""
              }`}
              onClick={() => {
                if (isEquals) equals();
                else if (isOp) choose(key as Op);
                else digit(key);
              }}
            >
              {key}
            </button>
          );
        })}
        <button
          type="button"
          className={`${styles.calcKey} ${styles.calcKeyClear}`}
          onClick={clear}
        >
          Clear
        </button>
      </div>
    </div>
  );
}
