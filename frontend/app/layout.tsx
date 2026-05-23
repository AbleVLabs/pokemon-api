import type { Metadata } from "next";
import { Geist, Geist_Mono, Orbitron } from "next/font/google";
import {
  ClerkProvider,
  Show,
  SignInButton,
  SignUpButton,
  UserButton,
} from "@clerk/nextjs";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// Orbitron — futuristic display font for the EtherDex brand.
const orbitron = Orbitron({
  variable: "--font-orbitron",
  subsets: ["latin"],
  weight: ["700", "900"],
});

export const metadata: Metadata = {
  title: "EtherDex — Your TCG Database",
  description: "Search trading card prices, rarities, and sets. Pokémon and more.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <ClerkProvider>
      <html
        lang="en"
        className={`${geistSans.variable} ${geistMono.variable} ${orbitron.variable} h-full antialiased`}
      >
        {/* Dark auth bar — matches the app theme, hairline border underneath */}
        <body className="min-h-full flex flex-col bg-zinc-950">
          <header className="flex justify-end items-center gap-3 px-6 py-3
                             bg-zinc-950 border-b border-zinc-800">
            <Show when="signed-out">
              <SignInButton mode="modal">
                <button className="text-sm text-zinc-300 hover:text-yellow-400 transition-colors">
                  Sign In
                </button>
              </SignInButton>
              <SignUpButton mode="modal">
                <button className="text-sm bg-yellow-400 hover:bg-yellow-300 text-black font-semibold px-4 py-1.5 rounded-lg transition-colors">
                  Sign Up
                </button>
              </SignUpButton>
            </Show>

            <Show when="signed-in">
              {/* Yellow ring makes the account icon easy to find */}
              <div className="ring-2 ring-yellow-400/60 rounded-full p-0.5">
                <UserButton
                  appearance={{
                    elements: {
                      userButtonAvatarBox: "w-11 h-11",
                    },
                  }}
                />
              </div>
            </Show>
          </header>

          {children}
        </body>
      </html>
    </ClerkProvider>
  );
}