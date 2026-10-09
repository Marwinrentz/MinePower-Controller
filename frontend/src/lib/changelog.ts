/** Changelog vom Backend (eine Quelle: CHANGELOG.md im Image). */
import { get, post } from "./api";
import type { User } from "./types";

export type ChangelogEntry = { version: string; date: string; new: string[]; changed: string[]; fixed: string[] };
export type Changelog = { version: string; entries: ChangelogEntry[]; unseen: ChangelogEntry[] };

export const loadChangelog = () => get<Changelog>("/api/system/changelog");
export const markSeen = () => post<User>("/api/auth/me/seen");

export const SECTIONS = [["new", "changelog.new"], ["changed", "changelog.changed"], ["fixed", "changelog.fixed"]] as const;
