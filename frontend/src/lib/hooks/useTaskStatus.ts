"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { authFetch } from "@/lib/auth";

export type TaskStatusValue = "idle" | "queued" | "running" | "done" | "error";

export interface TaskState<T = Record<string, unknown>> {
  status: TaskStatusValue;
  result: T | null;
  error: string | null;
}

/**
 * Polls `GET /api/tasks/{taskId}` every `intervalMs` milliseconds until the
 * task reaches a terminal state (done or error).  Polling stops automatically.
 *
 * Pass `null` as `taskId` to reset to the idle state (e.g. before submitting
 * a new job).
 *
 * @example
 * const { status, result, error } = useTaskStatus<OptimizerResult>(taskId);
 */
export function useTaskStatus<T = Record<string, unknown>>(
  taskId: string | null,
  intervalMs = 2000,
): TaskState<T> {
  const [state, setState] = useState<TaskState<T>>({
    status: "idle",
    result: null,
    error: null,
  });

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!taskId) {
      stopPolling();
      setState({ status: "idle", result: null, error: null });
      return;
    }

    setState({ status: "queued", result: null, error: null });

    const poll = async () => {
      try {
        const res = await authFetch(`/api/tasks/${taskId}`);
        if (!res.ok) {
          stopPolling();
          setState({ status: "error", result: null, error: `HTTP ${res.status}` });
          return;
        }
        const data: {
          status: string;
          result?: T;
          error?: string;
        } = await res.json();

        const status = data.status as TaskStatusValue;

        if (status === "done" || status === "error") {
          stopPolling();
          setState({
            status,
            result: data.result ?? null,
            error: data.error ?? null,
          });
        } else {
          setState((s) => ({ ...s, status }));
        }
      } catch {
        stopPolling();
        setState({ status: "error", result: null, error: "Network error" });
      }
    };

    poll(); // fire immediately on mount / taskId change
    timerRef.current = setInterval(poll, intervalMs);

    return stopPolling;
  }, [taskId, intervalMs, stopPolling]);

  return state;
}
