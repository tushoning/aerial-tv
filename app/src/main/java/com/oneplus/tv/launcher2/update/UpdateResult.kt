package com.oneplus.tv.launcher2.update

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Where the install session of an update from Control Center reports back (Updates.installBySession). */
class UpdateResult : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) = Updates.onSessionResult(context, intent)
}
