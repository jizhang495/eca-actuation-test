# ECA Testing Webapp - Frontend

Next.js frontend for the Electrochemical Actuator Testing Webapp.

## Technology Stack

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **UI Components**: shadcn/ui (Radix UI primitives)
- **Styling**: Tailwind CSS
- **Charts**: Recharts
- **Icons**: Lucide React

## Getting Started

### Install Dependencies

```bash
npm install
```

### Development Server

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

The frontend will proxy API requests to the backend at `http://localhost:8000`.

### Build for Production

```bash
npm run build
npm start
```

## Features

### Live Data Visualization
- Two real-time voltage graphs for DMM1 and DMM2
- Auto-scaling axes
- Smooth updates via WebSocket
- Configurable data point limits

### Instrument Control
- Dropdown selection for VISA resources
- Serial port selection for relay board
- Automatic instrument detection

### Stage Configuration
- Visual stage editors for voltage and relay control
- Add/remove stages dynamically
- Input validation
- Up to 10 stages per function

### Status Indicators
- Camera recording status with visual feedback
- Elapsed time display
- Session ID tracking
- Connection status alerts

## Project Structure

```
frontend/
├── src/
│   ├── app/                  # Next.js App Router
│   │   ├── layout.tsx       # Root layout
│   │   ├── page.tsx         # Main application page
│   │   └── globals.css      # Global styles
│   ├── components/          # React components
│   │   ├── ui/             # shadcn/ui components
│   │   │   ├── button.tsx
│   │   │   ├── card.tsx
│   │   │   ├── input.tsx
│   │   │   ├── label.tsx
│   │   │   └── select.tsx
│   │   ├── DMMGraph.tsx
│   │   ├── VoltageStageConfigurator.tsx
│   │   └── RelayStageConfigurator.tsx
│   └── lib/
│       └── utils.ts         # Utility functions
├── public/                  # Static assets
├── next.config.js          # Next.js configuration
├── tailwind.config.ts      # Tailwind configuration
├── tsconfig.json           # TypeScript configuration
└── package.json
```

## Configuration

### API Proxy

The frontend proxies API requests to avoid CORS issues during development. See `next.config.js`:

```javascript
async rewrites() {
  return [
    {
      source: '/api/:path*',
      destination: 'http://localhost:8000/api/:path*',
    },
  ];
}
```

### WebSocket Connection

WebSocket connects directly to the backend:

```typescript
const ws = new WebSocket('ws://localhost:8000/api/live');
```

For production, update the WebSocket URL in `src/app/page.tsx`.

## Customization

### Theme

Edit CSS variables in `src/app/globals.css` to customize colors:

```css
:root {
  --primary: 221.2 83.2% 53.3%;
  --secondary: 210 40% 96.1%;
  /* ... */
}
```

### Graph Settings

Adjust graph settings in `src/components/DMMGraph.tsx`:

```typescript
const MAX_DATA_POINTS = 500; // Limit displayed points
```

### Sampling Rate

Default sampling rate can be changed in `src/app/page.tsx`:

```typescript
const [samplingRate, setSamplingRate] = useState(10); // Hz
```

## Building Components

This project uses shadcn/ui components. To add new components:

```bash
npx shadcn-ui@latest add [component-name]
```

Example:
```bash
npx shadcn-ui@latest add dialog
```

## Development Tips

### Hot Reload
Next.js supports fast refresh. Changes to components will reflect immediately.

### Type Safety
All API responses should match the backend Pydantic models. Update types in components as needed.

### Performance
- Use React.memo() for expensive components
- Limit data points shown on graphs
- Throttle WebSocket updates if needed

## Deployment

### Static Export

For static hosting:

```bash
npm run build
```

Note: WebSocket functionality requires a running backend.

### Docker

Example Dockerfile:

```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
```

## Browser Support

- Chrome/Edge: Latest
- Firefox: Latest
- Safari: Latest

## Known Issues

- WebSocket reconnection may take a few seconds if backend restarts
- Large datasets (>1000 points) may slow down graph rendering

## License

See [LICENSE](../LICENSE) file for details.

