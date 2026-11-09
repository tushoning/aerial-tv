package app.orchard.tv.lite

import java.util.Calendar
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The same days as the full app's LunarTest, so the Calendar port reads the table the same way. */
class LunarTest {
    private fun text(y: Int, m: Int, d: Int) = Lunar.text(Calendar.getInstance().apply { clear(); set(y, m - 1, d, 12, 0) })

    @Test fun newYear() {
        assertEquals("正月初一", text(2026, 2, 17))
        assertEquals("腊月廿九", text(2026, 2, 16))
        assertEquals("正月初一", text(1901, 2, 19))
    }

    @Test fun ordinaryDays() {
        assertEquals("九月十六", text(2026, 10, 25))
        assertEquals("腊月初一", text(2100, 12, 31))
    }

    @Test fun leapMonths() {
        assertEquals("闰二月初一", text(2023, 3, 22))
        assertEquals("三月初一", text(2023, 4, 20))
        assertEquals("闰六月初一", text(2025, 7, 25))
        assertEquals("闰冬月初一", text(2033, 12, 22))
    }

    @Test fun outsideTheTable() {
        assertNull(text(1901, 2, 18))
        assertNull(text(2101, 3, 1))
    }
}
