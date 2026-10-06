package com.codeai.intellij

import com.intellij.openapi.Disposable
import com.intellij.openapi.components.Service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.progress.ProcessCanceledException
import com.intellij.util.concurrency.AppExecutorUtil
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/**
 * The one thread the plugin's own state lives on, per project.
 *
 * The VS Code extension this plugin mirrors runs on Node's single thread, and
 * its logic (which turn is in flight, which proposal is on screen) leans on
 * events being handled one at a time, in order. Backend events, panel messages
 * and timers are all queued here so the port keeps that guarantee; anything
 * that touches the IDE hops to the EDT from here.
 */
@Service(Service.Level.PROJECT)
internal class HostThread : Disposable {
    private val executor = AppExecutorUtil.createBoundedScheduledExecutorService("Code-AI host", 1)

    fun run(task: () -> Unit) {
        try {
            executor.execute(guard(task))
        } catch (_: RejectedExecutionException) {
            // The project is closing: there is nobody left to do this for.
        }
    }

    /** Run `task` after `delayMs`; cancel the returned handle to call it off. */
    fun later(delayMs: Long, task: () -> Unit): ScheduledFuture<*>? =
        try {
            executor.schedule(guard(task), delayMs, TimeUnit.MILLISECONDS)
        } catch (_: RejectedExecutionException) {
            null
        }

    fun every(periodMs: Long, task: () -> Unit): ScheduledFuture<*>? =
        try {
            executor.scheduleWithFixedDelay(guard(task), periodMs, periodMs, TimeUnit.MILLISECONDS)
        } catch (_: RejectedExecutionException) {
            null
        }

    /** One failing task must not take the queue, or a repeating timer, down with it. */
    private fun guard(task: () -> Unit) = Runnable {
        try {
            task()
        } catch (e: ProcessCanceledException) {
            throw e
        } catch (e: Throwable) {
            LOG.error("Code-AI host task failed", e)
        }
    }

    override fun dispose() {
        executor.shutdownNow()
    }

    private companion object {
        val LOG = Logger.getInstance(HostThread::class.java)
    }
}
