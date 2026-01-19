
export default function ComingSoon({ title }: { title: string }) {
    return (
        <div style={{
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
            alignItems: 'center',
            height: '100%',
            color: '#888'
        }}>
            <h1 style={{ fontSize: '3rem', marginBottom: '1rem' }}>🚧</h1>
            <h2>{title}</h2>
            <p>This feature is coming soon!</p>
        </div>
    );
}
