import { create } from "zustand";
import type { User } from "../lib/types";

type AuthState = {
  token: string | null;
  user: User | null;
  login: (token: string, user: User) => void;
  logout: () => void;
};

export const useAuth = create<AuthState>((set) => ({
  token: localStorage.getItem("sc_token"),
  user: JSON.parse(localStorage.getItem("sc_user") || "null"),
  login: (token, user) => {
    localStorage.setItem("sc_token", token);
    localStorage.setItem("sc_user", JSON.stringify(user));
    set({ token, user });
  },
  logout: () => {
    localStorage.removeItem("sc_token");
    localStorage.removeItem("sc_user");
    set({ token: null, user: null });
  },
}));
