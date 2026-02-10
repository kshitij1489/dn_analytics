import { createContext, useContext, useState, type ReactNode } from 'react';

interface NavigationContextType {
    activeTab: string;
    setActiveTab: (tab: string) => void;
    pageParams: Record<string, any>;
    navigate: (tab: string, params?: Record<string, any>) => void;
    clearParams: () => void;
}

const NavigationContext = createContext<NavigationContextType | undefined>(undefined);

export function NavigationProvider({ children, activeTab, setActiveTab }: { children: ReactNode, activeTab: string, setActiveTab: (tab: string) => void }) {
    const [pageParams, setPageParams] = useState<Record<string, any>>({});

    const navigate = (tab: string, params?: Record<string, any>) => {
        setPageParams(params || {});
        setActiveTab(tab);
    };

    const clearParams = () => {
        setPageParams({});
    };

    return (
        <NavigationContext.Provider value={{ activeTab, setActiveTab, pageParams, navigate, clearParams }}>
            {children}
        </NavigationContext.Provider>
    );
}

export function useNavigation() {
    const context = useContext(NavigationContext);
    if (context === undefined) {
        throw new Error('useNavigation must be used within a NavigationProvider');
    }
    return context;
}
