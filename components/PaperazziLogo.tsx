import logo from "@/assets/paperazzi-logo.png";

export function PaperazziLogo({ className = "" }: { className?: string }) {
  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <img
        src={logo.src}
        alt="Paperazzi"
        className="h-9 w-9 object-contain transition-transform duration-300 hover:rotate-[-6deg] hover:scale-110"
        style={{ filter: "drop-shadow(0 4px 10px rgba(0,0,0,0.35))" }}
      />
      <span className="text-lg font-semibold tracking-tight">
        Paper<span style={{ color: "var(--sunset)" }}>azzi</span>
      </span>
    </div>
  );
}
