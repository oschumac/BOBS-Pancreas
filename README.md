# BOBS Pancreas
### A DIY artificial pancreas system
**B**ased **O**n **B**olus/**S**uspend

BOBS Pancreas is a collection of components which can be assembled to build a DIY APS, which will work with some Medtronic pumps not supported by OpenAPS.

Fundamental features of BOBS Pancreas: 
* Use of a standard Medtronic remote control to operate the pump. The standard Medtronic remote has only two functions: Bolus and Suspend
* Use of a bolus/suspend based algorithm for managing glucose.
* Use of the OpenAPS toolkit and Nightscout for reading and reporting on data from the pump
* A reasonably compact form factor when used with the [Compact-APS](https://github.com/andrew-warrington/Compact-APS) enclosure, which was designed to be compatible with both OpenAPS and BOBS Pancreas.
* Being tested currently: The ability to automatically manage meals (no carb counting or manual boluses)

*In future, some user-oriented functionality capability may be added via a screen and buttons on the enclosure.*

## WARNING: 
BOBS Pancreas is not a plug-in-and-use solution. It cannot be used as-is and must be custom built by each person using it. This requires some understanding of electronics and programming. More importantly it requires a deep understanding your own case of Type 1 Diabetes.

BOBS Pancreas is perpetually a work in progress and as such comes with absolutely no warranty. In short, it doesn't work out of the box.

**_If you are looking for a mainstream, easier to build, well supported, better documented, and safer DIY artificial pancreas, please look into [OpenAPS](http://openaps.org)._**

## How is BOBS Pancreas different from OpenAPS?

BOBS Pancreas is fundamentally different from OpenAPS, not only in code, but in philosophy and approach as well. OpenAPS regulates glucose by enacting temporary changes to basal rates in the pump. As all changes are temporary, it remains a very safe solution, even when it fails.

BOBS Pancreas was created because OpenAPS cannot send commands to the Paradigm 754 pump. The only known way to send commands to this pump is via a Medtronic remote control. Unfortunately the Medtronic remote only has two functions: Bolus and Suspend. As the remote cannot enact temporary basal rates, a custom control loop is used which sends small boluses or suspends the pump as needed.

## How is BOBS Pancreas similar to OpenAPS?

Like OpenAPS, BOBS Pancreas leaves the insulin pump as the lead system. The insulin pump is unaltered and its warranty remains valid. If BOBS Pancreas fails, the insulin pump continues to function normally.

Also like OpenAPS, BOBS Pancreas relies on the fantastic OpenAPS toolkit for gathering data from the pump and running come key calculations such as IOB.

## So if the insulin pump is the lead system...
**I guess BOBS Pancreas is as safe as OpenAPS?**
Almost. **There is one scenario in particular** which a user of BOBS Pancreas needs to be aware of and vigilant about, described in the section below.

## Comparison: OpenAPS vs BOBS Pancreas

Advantages of OpenAPS over BOBS Pancreas|Advantages of BOBS Pancreas over OpenAPS
---------------------------------|---------------------------------
**OpenAPS is quieter.** BOBS Pancreas uses the Easy Bolus pump functionality, which makes noise each time it runs. As the algorithm works by issuing tiny boluses fairly often, the noise can become a major irritation.|**BOBS Pancreas can potentially operate autonomously once fully configured, tested, and monitored.** This means, no need to indicate when eating or to give manual boluses. BOBS Pancreas can take care of all that automatically, when its algorithm is properly tuned.
**OpenAPS is more tolerant of communication failure.** The OpenAPS core design takes into account the low reliability of communication between Carelink devices and pumps. The main OpenAPS tactic is to use temporary basal, which will, in the event that the APS fails, eventually cancel itself. With BOBS Pancreas, in low glucose situations the pump will be suspended. In the event BOBS Pancreas were to completely fail while the pump were suspended, the pump would never receive the command to resume. This risk is well mitigated by notifications and alarms on the pump itself, which allow the user to manually resume. Regardless, OpenAPS remains more robust in this area. Note, there are many software controls in BOBS Pancreas to deal with poor communication. The example given here is the highest risk currently assessed regarding BOBS Pancreas.|**BOBS Pancreas adds model 754 to the list of compatible Medtronic pumps.** Complete list which should work with BOBS Pancreas: MiniMed Paradigm 511, 512, 515, 522, 554, 712, 715, 722, 754 
**OpenAPS is easier to implement.** BOBS Pancreas requires precision modification of the Medtronic standard remote control to be able to send "button press" commands from a Raspberry Pi. This work requires some ability with a soldering iron and if done incorrectly, could lead to errant bolus commands or other issues. In the event these issues were to occur, they might be mitigated via software controls or built-in safety measures on the pump, but better to be confident in your soldering abilities, or not undertake the project at all.|
**OpenAPS is capable of finer adjustments.** The minimum amount of insulin a Medtronic remote can bolus is 0.1 units. While this may seem like a very small amount, there are often cases when the glucose is near normal and there is already insulin on board, where a finer ajustment (say, 0.04 units) would be better. Because basal rates deliver such minuscule quantities of insulin, OpenAPS is capable of making such adjustments.|

Further information about BOBS Pancreas hardware and software will be posted to this repository in January 2016.
