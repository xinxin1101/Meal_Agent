import { useEffect, useRef } from "react";
import { Icon } from "../../components/ui/Icon";
import { useUserProfile } from "../../context/UserProfileContext";
import { ProfileFields } from "./ProfileFields";

export function ProfileDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { validationErrors, saveProfile, profileSaving } = useUserProfile();
  const closeButton = useRef<HTMLButtonElement>(null);
  const closeHandler = useRef(onClose);
  closeHandler.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    const overflow = document.body.style.overflow;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") closeHandler.current(); };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", escape);
    closeButton.current?.focus();
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", escape); previousFocus?.focus(); };
  }, [open]);
  if (!open) return null;
  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="profile-drawer-title">
      <header><div><p className="eyebrow">健康成年人合同</p><h2 id="profile-drawer-title">编辑规划档案</h2></div><button ref={closeButton} className="icon-button" type="button" onClick={onClose} aria-label="关闭档案编辑"><Icon name="close" /></button></header>
      <ProfileFields radioGroupName="drawer-allergen-status" />
      <footer><p>{validationErrors.length ? `还有 ${validationErrors.length} 项需要处理` : "档案完整，可以保存并用于规划"}</p><button className="button button-primary" type="button" onClick={() => void saveProfile().then(onClose)} disabled={validationErrors.length > 0 || profileSaving}>{profileSaving ? "保存中…" : "保存档案"}</button></footer>
    </aside>
  </div>;
}
