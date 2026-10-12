import { type ReactNode, createContext, useContext, useState } from "react";

const OwnerContext = createContext<{
	token: string | null;
	setToken: (token: string | null) => void;
}>({ token: null, setToken: () => {} });

export function OwnerProvider({ children }: { children: ReactNode }) {
	const [token, setToken] = useState<string | null>(null);
	return (
		<OwnerContext.Provider value={{ token, setToken }}>
			{children}
		</OwnerContext.Provider>
	);
}

export function useOwner() {
	return useContext(OwnerContext);
}
