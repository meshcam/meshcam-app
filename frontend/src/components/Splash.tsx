import { LogIn } from 'lucide-react'
import { signIn } from '../api'

export default function Splash() {
  return (
    <div className="splash">
      <h1 className="splash-title">🦌 MeshCam</h1>
      <p className="splash-subtitle">LoRa mesh trail cameras</p>
      <button type="button" className="btn btn-primary splash-signin" onClick={signIn}>
        <LogIn size={18} aria-hidden="true" />
        Sign in
      </button>
    </div>
  )
}
