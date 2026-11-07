import { ChevronRight, MessageCircle } from "lucide-react";
import { useState } from "react";
import { createPortal } from "react-dom";
import { Modal } from "./UI";
import "./AuthorContact.css";

function AuthorContactDialog({ onClose }: { onClose: () => void }) {
  const [imageState, setImageState] = useState<"loading" | "ready" | "missing">(
    "loading",
  );

  return (
    <Modal title="联系作者" onClose={onClose}>
      <div className="modal-body author-contact-body">
        {imageState !== "missing" && (
          <img
            className="author-contact-qr"
            src={import.meta.env.BASE_URL + "author-wechat.jpg"}
            alt="添加作者的微信二维码"
            width={888}
            height={1128}
            decoding="async"
            hidden={imageState !== "ready"}
            onLoad={() => setImageState("ready")}
            onError={() => setImageState("missing")}
          />
        )}
        {imageState === "ready" ? (
          <p>打开微信扫一扫，添加作者</p>
        ) : (
          <p role="status">
            {imageState === "loading"
              ? "正在加载微信二维码…"
              : "暂未配置微信二维码"}
          </p>
        )}
      </div>
    </Modal>
  );
}

export default function AuthorContact() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="sidebar-contact"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        <MessageCircle size={15} aria-hidden="true" />
        联系作者
        <ChevronRight size={12} aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <AuthorContactDialog onClose={() => setOpen(false)} />,
          document.body,
        )}
    </>
  );
}
