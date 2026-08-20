import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";
import { apiClient } from "../api/client";
import type { AccountProfile, UserProfile } from "../api/types";
import { useAuth } from "./AuthContext";

export type ProfileDraft = Omit<UserProfile, "profile_snapshot_id" | "adult_confirmed" | "age_years"> & { age_years: string };
export type AllergenStatus = "unconfirmed" | "none" | "specified";

const initialProfile: ProfileDraft = {
  age_years: "28", nutrition_parameter_sex: "unspecified", height_cm: "170", weight_kg: "65",
  activity_level: "moderate", goal: "maintain", allergens: [], avoidances: [],
};

type ProfileContextValue = {
  profile: ProfileDraft;
  allergenStatus: AllergenStatus;
  updateProfile: <K extends keyof ProfileDraft>(field: K, value: ProfileDraft[K]) => void;
  setAllergenStatus: (status: AllergenStatus) => void;
  validationErrors: string[];
  toContract: (snapshotId: string) => UserProfile;
  profileVersion: number;
  profileLoading: boolean;
  profileSaving: boolean;
  saveProfile: () => Promise<void>;
};

const UserProfileContext = createContext<ProfileContextValue | null>(null);

export function validateHealthyAdultProfile(profile: ProfileDraft, allergenStatus: AllergenStatus = "none"): string[] {
  const errors: string[] = [];
  if (!Number.isInteger(Number(profile.age_years)) || Number(profile.age_years) < 18) errors.push("年龄必须是 18 岁及以上的整数。");
  if (!(Number(profile.height_cm) > 0)) errors.push("身高必须大于 0。");
  if (!(Number(profile.weight_kg) > 0)) errors.push("体重必须大于 0。");
  if (allergenStatus === "unconfirmed") errors.push("请明确确认过敏原信息；不能将空白视为安全。");
  if (allergenStatus === "specified" && !profile.allergens.length) errors.push("请至少填写一种明确的过敏原。");
  return errors;
}

export function UserProfileProvider({ children }: { children: ReactNode }) {
  const { account } = useAuth();
  const [profile, setProfile] = useState<ProfileDraft>(initialProfile);
  const [allergenStatus, setAllergenStatus] = useState<AllergenStatus>("unconfirmed");
  const [profileVersion, setProfileVersion] = useState(0);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileSaving, setProfileSaving] = useState(false);
  useEffect(() => {
    setProfileLoading(true);
    void apiClient.get<AccountProfile>("/v1/account/profile").then((saved) => {
      setProfileVersion(saved.profile_version);
      if (saved.profile) {
        const { profile_snapshot_id: _snapshot, adult_confirmed: _adult, age_years, ...rest } = saved.profile;
        setProfile({ ...rest, age_years: String(age_years) });
        setAllergenStatus(saved.profile.allergens.length ? "specified" : "none");
      } else { setProfile(initialProfile); setAllergenStatus("unconfirmed"); }
    }).finally(() => setProfileLoading(false));
  }, [account.user_id]);
  const validationErrors = useMemo(() => validateHealthyAdultProfile(profile, allergenStatus), [profile, allergenStatus]);
  const value: ProfileContextValue = {
    profile,
    allergenStatus,
    updateProfile: (field, fieldValue) => setProfile((current) => ({ ...current, [field]: fieldValue })),
    setAllergenStatus: (status) => {
      setAllergenStatus(status);
      if (status !== "specified") setProfile((current) => ({ ...current, allergens: [] }));
    },
    validationErrors,
    profileVersion,
    profileLoading,
    profileSaving,
    saveProfile: async () => {
      const errors = validateHealthyAdultProfile(profile, allergenStatus);
      if (errors.length) throw new Error(errors.join(" "));
      setProfileSaving(true);
      try {
        const saved = await apiClient.put<AccountProfile>("/v1/account/profile", { expected_profile_version: profileVersion, profile: { ...profile, profile_snapshot_id: `profile-${crypto.randomUUID()}`, adult_confirmed: true, age_years: Number(profile.age_years) } });
        setProfileVersion(saved.profile_version);
      } finally { setProfileSaving(false); }
    },
    toContract: (snapshotId) => {
      const errors = validateHealthyAdultProfile(profile, allergenStatus);
      if (errors.length) throw new Error(errors.join(" "));
      return { ...profile, profile_snapshot_id: snapshotId, adult_confirmed: true, age_years: Number(profile.age_years) };
    },
  };
  return <UserProfileContext.Provider value={value}>{children}</UserProfileContext.Provider>;
}

export function useUserProfile(): ProfileContextValue {
  const context = useContext(UserProfileContext);
  if (!context) throw new Error("useUserProfile must be used inside UserProfileProvider");
  return context;
}
